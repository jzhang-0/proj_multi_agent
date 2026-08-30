"""FastAPI 应用工厂:本机认证会话 + 只读 snapshot API + versioned 实时流。

`/api/v1/*` 只读、无副作用，只经控制面(`control/`)调用领域层；不建立第二套
状态库，不触发 tmux resize/send_keys(架构 §1)。鉴权、Host 校验、
Cache-Control 头统一在这里处理，DTO 组装在 `web.snapshots`；WS 在
`web.stream`(WEB-004)。

`lifespan` 起常驻 `MemberStatusService`、`HealthMonitor` 与 `EventHub`。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.resources
import logging
import mimetypes
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect

from bus.audit import AuditLog
from control.delivery import DeliveryPump, MutePolicy
from control.health import HealthMonitor
from control.lease import HubDeliveryLease, MemberLeaseManager, leases_root
from control.member_admin import MemberAdminController, MemberAdminError
from control.members import MemberStatusService
from control.timeline import TimelineCache
from roster.schema import RosterError
from tmuxctl import TmuxError, WindowSizeGuard
from web.actions import (
    download_attachment,
    read_image_body,
    read_message_payload,
    send_message,
    upload_attachment,
)
from web.attach import AttachRegistry, run_attach_connection
from web.auth import COOKIE_NAME, WebSession
from web.context import build_context
from web.errors import ApiError, ApiJSONResponse, register_error_handlers
from web.snapshots import (
    bootstrap_dto,
    health_dto,
    members_dto,
    session_dto,
    task_detail_dto,
    team_dto,
    timeline_body_text,
    timeline_dto,
    vocabulary_dto,
    work_dto,
    workspace_dto,
)
from web.state import RevisionTracker
from web.stream import EventHub, StreamSettings, handle_stream
from web.terminal import HEARTBEAT_INTERVAL, ConnectionState, MirrorHub, run_mirror_connection

API_PREFIX = "/api/v1"

#: BUG(T-025):WS 握手/票据拒绝此前完全不落日志，human 实机复现
#: unauthorized 时无法判断 Host/Origin/cookie 三道校验哪一道没过。
#: uvicorn 默认 `log_level="warning"`(见 web/cli.py)，故这里用 WARNING，
#: 保证不额外调命令行参数也能在终端看到。
logger = logging.getLogger("web.app")

#: WS 握手拒绝用的私有关闭码(RFC 6455 4000-4999 段)；协议文档未定专门取值，
#: 这里的编号只是内部约定,与 HTTP 侧 §2.4 错误码同义对照(unauthorized/
#: not-found/service-unavailable)，方便复审核对。
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_NOT_FOUND = 4404
WS_CLOSE_UNAVAILABLE = 4503


def allowed_hosts(port: int) -> frozenset[str]:
    """`Host` 头白名单(架构 §6.3):只接受本机地址 + 当前监听端口。"""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def allowed_origins(port: int) -> frozenset[str]:
    """WS 握手的 `Origin` 白名单(架构 §6.3、terminal-protocol §2):只认本机。"""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def _error(code: str, message: str, *, status_code: int, domain: str = "web") -> ApiJSONResponse:
    """§2.4 统一错误模型；§2.1 要求 charset=utf-8，走 `ApiJSONResponse`。"""
    return ApiJSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "domain": domain}},
    )


def _health_page() -> str:
    resource = importlib.resources.files("web").joinpath("static", "health.html")
    return resource.read_text(encoding="utf-8")


def _static_response(*parts: str) -> Response | None:
    """从 wheel 内 `web/static` 读取资源；拒绝路径跳转，不依赖源码目录。"""
    if not parts or any(not part or part in {".", ".."} or "/" in part for part in parts):
        return None
    resource = importlib.resources.files("web").joinpath("static", *parts)
    if not resource.is_file():
        return None
    media_type = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
    return Response(content=resource.read_bytes(), media_type=media_type)


def _spa_index() -> Response:
    packaged = _static_response("index.html")
    return packaged if packaged is not None else HTMLResponse(_health_page())


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """进程启动时解析一次工作区，起常驻成员/健康监视与事件流；退出时干净收尾。

    tmux 不可用(或工作区未登记)时 `member_status.can_monitor` 为假，
    `MemberStatusService.run()` 直接返回(`control/members.py`)——应用照常
    启动。但没有后台监视任务就没有人把 `alive` 从构造默认值 `True` 纠正
    过来(评审 opus 实测发现)：`ActivityTracker.__init__` 默认 `_alive=True`
    (`tmuxctl/activity.py:69`)，对齐 `console/app.py:303-305` 的做法，
    在这里显式 `set_alive(False)`。
    """
    settings: StreamSettings = app.state.stream_settings
    ctx = build_context()
    app.state.tmux = ctx.tmux
    app.state.lease_manager = (
        MemberLeaseManager(leases_root(ctx.workspace)) if ctx.workspace is not None else None
    )
    muted: set[str] = set()
    member_admin = None
    if ctx.workspace is not None and ctx.paths is not None and ctx.tmux is not None:
        member_admin = MemberAdminController(ctx.workspace, ctx.tmux, ctx.paths, muted=muted)
    app.state.member_admin = member_admin
    names = member_admin.member_names() if member_admin is not None else ctx.names
    sources = member_admin.sources() if member_admin is not None else None
    member_status = MemberStatusService(names, ctx.tmux, sources=sources)
    app.state.member_status = member_status
    health: HealthMonitor | None = None
    if ctx.paths is not None:
        health = HealthMonitor(ctx.paths, names, ctx.tmux, interval=settings.health_interval_s)
    app.state.health_monitor = health
    hub = EventHub(
        tracker=app.state.revisions,
        cache=app.state.timeline_cache,
        member_status=member_status,
        health=health,
        tmux=ctx.tmux,
        settings=settings,
        names_provider=member_admin.member_names if member_admin is not None else None,
    )
    app.state.stream = hub
    window_guard = WindowSizeGuard()
    app.state.window_guard = window_guard
    delivery_pump = None
    if ctx.workspace is not None and ctx.paths is not None and ctx.tmux is not None:
        hub_lease = HubDeliveryLease(
            leases_root(ctx.workspace) / "hub.json",
            f"web:{os.getpid()}:{uuid.uuid4().hex[:8]}",
        )
        delivery_pump = DeliveryPump(
            ctx.paths,
            policy=MutePolicy(muted),
            lease=hub_lease,
            tmux=ctx.tmux,
        )
        delivery_pump.start()
    app.state.delivery_pump = delivery_pump
    tasks: list[asyncio.Task[None]] = []
    if member_status.can_monitor:
        tasks.append(asyncio.create_task(member_status.run(), name="web-member-status"))
    else:
        for name in names:
            member_status.set_alive(name, False)
    tasks.append(asyncio.create_task(hub.run(), name="web-event-hub"))
    try:
        yield
    finally:
        hub.stop()
        member_status.stop()
        if health is not None:
            health.stop()
        if delivery_pump is not None:
            delivery_pump.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        window_guard.close()


def create_app(
    *,
    session: WebSession,
    port: int,
    stream_settings: StreamSettings | None = None,
) -> FastAPI:
    """构造应用;`session` 与 `port` 由 `web.cli.main` 每次启动时生成/确定。

    不注册 `CORSMiddleware`(架构 §6.3 明确要求不设任何 CORS 响应头);
    也关掉自带的 `/docs`、`/redoc`、`/openapi.json`,减少未鉴权即可访问
    的面。
    """
    app = FastAPI(
        title="amux web",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    hosts = allowed_hosts(port)
    origins = allowed_origins(port)
    app.state.stream_settings = stream_settings or StreamSettings()
    app.state.revisions = RevisionTracker()
    app.state.timeline_cache = TimelineCache()
    # lifespan 填 tmux/member_status/health_monitor/stream/lease_manager；这里
    # 先占位默认值，ASGI 服务器总会先跑 lifespan 再派发请求，但没有它们
    # （比如构造 TestClient 时忘了 `with`）不该让 app.state 缺失属性冒
    # AttributeError→500，而是走 §2.4 的 503(见 API router 的 require_runtime
    # 依赖)。
    app.state.tmux = None
    app.state.member_status = None
    app.state.health_monitor = None
    app.state.stream = None
    app.state.lease_manager = None
    app.state.member_admin = None
    app.state.delivery_pump = None
    app.state.window_guard = None
    app.state.mirror_hub = MirrorHub()
    app.state.attach_registry = AttachRegistry()
    # 测试可收窄以避免等一个真实心跳周期(默认 5s)；生产不改。
    app.state.heartbeat_interval = HEARTBEAT_INTERVAL
    register_error_handlers(app)

    @app.middleware("http")
    async def _security(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        host = request.headers.get("host", "")
        if host not in hosts:
            return _error(
                "unauthorized",
                f"不接受的 Host: {host!r}(防 DNS rebinding，见架构 §6.3)",
                status_code=401,
            )
        response = await call_next(request)
        path = request.url.path
        if path.startswith(f"{API_PREFIX}/") and path != f"{API_PREFIX}/vocabulary":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def index(request: Request, token: str | None = None) -> Response:
        if token is not None:
            if not session.verify_token(token):
                return _error("unauthorized", "token 无效", status_code=401)
            # token 留在地址栏会进历史记录与 referrer；换出 cookie 后立刻
            # 302 到不带 token 的地址(架构 §6.2 第 2 步)。
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                COOKIE_NAME,
                session.session_id,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response
        if not session.verify_cookie(request.cookies.get(COOKIE_NAME)):
            return _error(
                "unauthorized",
                "缺少有效会话，请用启动时终端打印的地址访问",
                status_code=401,
            )
        return _spa_index()

    def require_session(request: Request) -> None:
        if not session.verify_cookie(request.cookies.get(COOKIE_NAME)):
            raise ApiError(
                "unauthorized",
                "缺少有效会话，请用启动时终端打印的地址访问",
                status_code=401,
            )

    def require_runtime() -> None:
        """lifespan 未运行时不把未初始化的 app.state 冒成 500。"""
        if app.state.member_status is None:
            raise ApiError(
                "work-unavailable",
                "Web snapshot 运行时尚未就绪",
                status_code=503,
                domain="web",
            )

    def require_write_session(request: Request) -> None:
        if not session.verify_cookie(request.headers.get("x-amux-session")):
            raise ApiError(
                "unauthorized",
                "写操作缺少有效 X-Amux-Session",
                status_code=401,
            )

    def current_context():
        ctx = build_context(tmux=app.state.tmux)
        admin = app.state.member_admin
        if admin is not None and ctx.workspace is not None:
            ctx = replace(ctx, names=admin.member_names())
        return ctx

    def require_member_admin() -> MemberAdminController:
        admin = app.state.member_admin
        if admin is None:
            raise ApiError(
                "control-unavailable",
                "成员控制需要已登记工作区与可用 tmux",
                status_code=503,
                domain="control",
            )
        return admin

    async def control_body(request: Request) -> dict:
        try:
            raw = await request.json()
        except (TypeError, ValueError):
            raise ApiError(
                "invalid-request", "请求体必须是 JSON 对象", status_code=400, domain="control"
            ) from None
        if not isinstance(raw, dict):
            raise ApiError(
                "invalid-request", "请求体必须是 JSON 对象", status_code=400, domain="control"
            )
        if "actor" in raw:
            raise ApiError(
                "invalid-request",
                "actor 只取认证上下文，客户端不得自报",
                status_code=400,
                domain="control",
            )
        return raw

    async def run_admin(callable_, *args):
        try:
            return await asyncio.to_thread(callable_, *args)
        except (MemberAdminError, RosterError, TmuxError, OSError) as exc:
            raise ApiError(
                "invalid-member-action", str(exc), status_code=400, domain="control"
            ) from exc

    def sync_admin_names(admin: MemberAdminController) -> None:
        names = admin.member_names()
        app.state.member_status.track(names, sources=admin.sources())
        if app.state.health_monitor is not None:
            app.state.health_monitor.track(names)

    api = APIRouter(
        prefix=API_PREFIX,
        dependencies=[Depends(require_session), Depends(require_runtime)],
        default_response_class=ApiJSONResponse,
    )

    @api.get("/session")
    async def get_session() -> dict:
        return session_dto(
            app.state.revisions,
            actor=session.actor,
            write_token=session.session_id,
        )

    @api.get("/bootstrap")
    async def get_bootstrap() -> dict:
        ctx = current_context()
        return bootstrap_dto(
            ctx,
            app.state.revisions,
            app.state.timeline_cache,
            app.state.member_status,
            app.state.health_monitor,
            actor=session.actor,
            write_token=session.session_id,
        )

    @api.get("/vocabulary")
    async def get_vocabulary() -> dict:
        return vocabulary_dto(app.state.revisions)

    @api.get("/workspace")
    async def get_workspace() -> dict:
        ctx = current_context()
        return workspace_dto(ctx, app.state.revisions)

    @api.get("/team")
    async def get_team() -> dict:
        ctx = current_context()
        return team_dto(ctx, app.state.revisions)

    @api.get("/work")
    async def get_work() -> dict:
        ctx = current_context()
        return work_dto(ctx, app.state.revisions)

    @api.get("/work/tasks/{task_id}")
    async def get_task_detail(task_id: str) -> dict:
        ctx = current_context()
        return task_detail_dto(ctx, app.state.revisions, app.state.timeline_cache, task_id)

    @api.get("/timeline")
    async def get_timeline(
        category: str = "all",
        limit: int = 200,
        before_seq: int | None = None,
    ) -> dict:
        if not 1 <= limit <= 1000:
            raise ApiError(
                "invalid-request", "limit 必须在 1..1000 之间", status_code=400, domain="timeline"
            )
        ctx = current_context()
        return timeline_dto(
            ctx,
            app.state.revisions,
            app.state.timeline_cache,
            category=category,
            limit=limit,
            before_seq=before_seq,
        )

    @api.get("/timeline/{seq}/body")
    async def get_timeline_body(seq: int) -> Response:
        ctx = current_context()
        text = timeline_body_text(ctx, app.state.timeline_cache, seq)
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment"},
        )

    @api.get("/members")
    async def get_members() -> dict:
        ctx = current_context()
        return members_dto(ctx, app.state.revisions, app.state.member_status)

    @api.get("/health")
    async def get_health() -> dict:
        ctx = current_context()
        return health_dto(ctx, app.state.revisions, app.state.health_monitor)

    @api.post("/attachments", dependencies=[Depends(require_write_session)])
    async def post_attachment(request: Request) -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return upload_attachment(ctx, await read_image_body(request))

    @api.get("/attachments/{attachment_id}")
    async def get_attachment(attachment_id: str) -> Response:
        ctx = build_context(tmux=app.state.tmux)
        return download_attachment(ctx, attachment_id)

    @api.post("/messages", dependencies=[Depends(require_write_session)])
    async def post_message(request: Request) -> dict:
        ctx = current_context()
        payload = await read_message_payload(request)
        return send_message(ctx, actor=session.actor, payload=payload)

    @api.get("/member-management")
    async def get_member_management() -> dict:
        admin = require_member_admin()
        return await run_admin(admin.listing)

    @api.post("/members", dependencies=[Depends(require_write_session)])
    async def post_member(request: Request) -> dict:
        admin = require_member_admin()
        raw = await control_body(request)
        result = await run_admin(admin.add, str(raw.get("name", "")))
        sync_admin_names(admin)
        return result

    @api.delete("/members/{name}", dependencies=[Depends(require_write_session)])
    async def delete_member(name: str) -> dict:
        admin = require_member_admin()
        result = await run_admin(admin.remove, name)
        sync_admin_names(admin)
        return result

    @api.post("/members/adopt", dependencies=[Depends(require_write_session)])
    async def post_adopt(request: Request) -> dict:
        admin = require_member_admin()
        raw = await control_body(request)
        result = await run_admin(admin.adopt, str(raw.get("name", "")))
        sync_admin_names(admin)
        return result

    @api.post("/members/{name}/mute", dependencies=[Depends(require_write_session)])
    async def post_mute(name: str, request: Request) -> dict:
        await control_body(request)
        return await run_admin(require_member_admin().toggle_mute, name)

    @api.post("/members/{name}/{action}/confirm", dependencies=[Depends(require_write_session)])
    async def post_control_confirm(name: str, action: str, request: Request) -> dict:
        await control_body(request)
        admin = require_member_admin()
        return await run_admin(admin.confirm, session.actor, name, action)

    @api.post("/members/{name}/{action}", dependencies=[Depends(require_write_session)])
    async def post_member_action(name: str, action: str, request: Request) -> dict:
        raw = await control_body(request)
        admin = require_member_admin()
        if action == "interrupt":
            feedback = await run_admin(admin.interrupt, name)
        elif action == "up":
            feedback = await run_admin(admin.up, name)
        elif action == "attach":
            return await run_admin(admin.authorize_attach, session.actor, name)
        elif action == "direct":
            return await run_admin(admin.authorize_direct, session.actor, name)
        elif action in {"terminate", "restart", "down"}:
            token = raw.get("confirm_token")
            if not isinstance(token, str) or not token:
                raise ApiError(
                    "confirmation-required",
                    "危险动作需要一次性 confirm_token",
                    status_code=409,
                    domain="control",
                )
            feedback = await run_admin(
                getattr(admin, action), session.actor, name, token
            )
        else:
            raise ApiError(
                "not-found", f"未知成员动作: {action}", status_code=404, domain="control"
            )
        return admin.feedback(feedback)

    @app.websocket(f"{API_PREFIX}/terminal/{{member}}/mirror")
    async def terminal_mirror(websocket: WebSocket, member: str) -> None:
        """WEB-007 §2/§4-§7:镜像 + 租约串行直连输入,单一端点承载全部消息类型。

        HTTP 中间件不拦截 WebSocket scope(Starlette 的 `@app.middleware("http")`
        只挂 `http` scope)，Host/Origin/cookie 三项鉴权必须在这里手动做完。

        评审(opus)实测指出:`close()` 若在 `accept()` 之前调用,ASGI 服务器
        (uvicorn)会把它变成握手阶段的 HTTP 403——浏览器 `WebSocket` API 不
        暴露失败握手的状态码/正文给 JS,`onclose` 只会拿到无信息量的 1006。
        必须先 `accept()` 再 `close(code=...)`,自定义关闭码才能真正送达
        客户端(已用真实 uvicorn + `websockets` 客户端验证)。接受后立即关闭
        不产生订阅/租约/进程,不算真正建立连接。
        """

        async def reject(code: int, reason: str, *, check: str) -> None:
            logger.warning(
                "ws_reject endpoint=mirror member=%s check=%s code=%d host=%r origin=%r",
                member,
                check,
                code,
                host,
                origin,
            )
            await websocket.accept()
            await websocket.close(code=code, reason=reason)

        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin", "")
        if host not in hosts:
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="host")
            return
        if origin not in origins:
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="origin")
            return
        if not session.verify_cookie(websocket.cookies.get(COOKIE_NAME)):
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="cookie")
            return

        ctx = current_context()
        if (
            ctx.workspace is None
            or ctx.tmux is None
            or app.state.lease_manager is None
            or app.state.member_admin is None
        ):
            await reject(WS_CLOSE_UNAVAILABLE, "unavailable", check="runtime")
            return
        if member not in ctx.names:
            await reject(WS_CLOSE_NOT_FOUND, "member-not-found", check="member")
            return
        if not await asyncio.to_thread(ctx.tmux.has_session, member):
            await reject(WS_CLOSE_NOT_FOUND, "session-not-found", check="tmux-session")
            return

        await websocket.accept()
        conn_id = uuid.uuid4().hex
        owner = f"web:{session.session_id}:{conn_id}"
        state = ConnectionState(
            member=member,
            owner=owner,
            tmux=ctx.tmux,
            lease_manager=app.state.lease_manager,
            audit=AuditLog(ctx.paths),
            hub=app.state.mirror_hub,
            window_guard=app.state.window_guard,
            authorize=lambda token: app.state.member_admin.consume_direct(
                session.actor, member, token
            ),
            heartbeat_interval=app.state.heartbeat_interval,
        )
        with contextlib.suppress(WebSocketDisconnect):
            await run_mirror_connection(websocket, state)

    @app.websocket(f"{API_PREFIX}/terminal/{{member}}/attach")
    async def terminal_attach(websocket: WebSocket, member: str) -> None:
        async def reject(code: int, reason: str, *, check: str) -> None:
            logger.warning(
                "ws_reject endpoint=attach member=%s check=%s code=%d host=%r origin=%r",
                member,
                check,
                code,
                host,
                origin,
            )
            await websocket.accept()
            await websocket.close(code=code, reason=reason)

        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin", "")
        if host not in hosts:
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="host")
            return
        if origin not in origins:
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="origin")
            return
        if not session.verify_cookie(websocket.cookies.get(COOKIE_NAME)):
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized", check="cookie")
            return
        ctx = current_context()
        if (
            ctx.workspace is None
            or ctx.paths is None
            or ctx.tmux is None
            or app.state.lease_manager is None
            or app.state.member_admin is None
        ):
            await reject(WS_CLOSE_UNAVAILABLE, "unavailable", check="runtime")
            return
        if member not in ctx.names:
            await reject(WS_CLOSE_NOT_FOUND, "member-not-found", check="member")
            return
        await websocket.accept()
        owner = f"web-attach:{session.session_id}:{uuid.uuid4().hex}"
        with contextlib.suppress(WebSocketDisconnect):
            await run_attach_connection(
                websocket,
                member=member,
                owner=owner,
                tmux=ctx.tmux,
                lease_manager=app.state.lease_manager,
                audit=AuditLog(ctx.paths),
                registry=app.state.attach_registry,
                authorize=lambda token: app.state.member_admin.consume_attach(
                    session.actor, member, token
                ),
                window_guard=app.state.window_guard,
                heartbeat_interval=app.state.heartbeat_interval,
            )

    app.include_router(api)

    @app.get("/assets/{asset_path:path}")
    async def static_asset(request: Request, asset_path: str) -> Response:
        require_session(request)
        response = _static_response("assets", *asset_path.split("/"))
        if response is None:
            return _error("not-found", "静态资源不存在", status_code=404)
        return response

    @app.get("/THIRD_PARTY_LICENSES.json")
    async def third_party_licenses(request: Request) -> Response:
        require_session(request)
        response = _static_response("THIRD_PARTY_LICENSES.json")
        if response is None:
            return _error("not-found", "第三方许可证清单不存在", status_code=404)
        return response

    @app.get("/{spa_path:path}")
    async def spa_fallback(request: Request, spa_path: str) -> Response:
        """支持刷新 `/workspace`、`/task/<id>`、`/help`、`/member/<name>/terminal` 等前端路由。"""
        require_session(request)
        is_terminal_route = spa_path.startswith("member/") and spa_path.endswith("/terminal")
        if (
            spa_path in {"workspace", "timeline", "help"}
            or spa_path.startswith("task/")
            or is_terminal_route
        ):
            return _spa_index()
        return _error("not-found", "页面不存在", status_code=404)

    @app.websocket(f"{API_PREFIX}/stream")
    async def stream_endpoint(websocket: WebSocket) -> None:
        await handle_stream(
            websocket,
            session=session,
            port=port,
            hub=app.state.stream,
        )

    return app
