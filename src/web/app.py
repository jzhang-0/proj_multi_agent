"""FastAPI 应用工厂(WEB-003):本机认证会话 + 只读 snapshot API + 最小静态健康页。

`/api/v1/*` 只读、无副作用，只经控制面(`control/`)调用领域层；不建立第二套
状态库，不触发 tmux resize/send_keys(架构 §1)。鉴权、Host 校验、
Cache-Control 头统一在这里处理，DTO 组装在 `web.snapshots`。

`lifespan` 起一个常驻 `MemberStatusService` 并调度它的 `run()`(对齐
`console.app` 的接线，见 `ConsoleApp.on_mount`/`on_unmount`):`/api/v1/members`
的 `state`/`silent_for` 依赖持续喂 `ActivityTracker` 的后台 pane 输出监听，
每请求现建一个从未被喂过样本的 `MemberStatusService` 只会看到恒定的
idle/None(评审 opus 实测发现)。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.resources
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect

from bus.audit import AuditLog
from control.lease import MemberLeaseManager, leases_root
from control.members import MemberStatusService
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
from web.state import RevisionTracker, TimelineCache
from web.terminal import HEARTBEAT_INTERVAL, ConnectionState, MirrorHub, run_mirror_connection

API_PREFIX = "/api/v1"

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


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """进程启动时解析一次工作区，起常驻成员状态监视；退出时干净收尾。

    tmux 不可用(或工作区未登记)时 `member_status.can_monitor` 为假，
    `MemberStatusService.run()` 直接返回(`control/members.py`)——应用照常
    启动。但没有后台监视任务就没有人把 `alive` 从构造默认值 `True` 纠正
    过来(评审 opus 实测发现)：`ActivityTracker.__init__` 默认 `_alive=True`
    (`tmuxctl/activity.py:69`)，对齐 `console/app.py:303-305` 的做法，
    在这里显式 `set_alive(False)`。
    """
    ctx = build_context()
    app.state.tmux = ctx.tmux
    app.state.lease_manager = (
        MemberLeaseManager(leases_root(ctx.workspace)) if ctx.workspace is not None else None
    )
    member_status = MemberStatusService(ctx.names, ctx.tmux)
    app.state.member_status = member_status
    monitor_task: asyncio.Task[None] | None = None
    if member_status.can_monitor:
        monitor_task = asyncio.create_task(member_status.run())
    else:
        for name in ctx.names:
            member_status.set_alive(name, False)
    try:
        yield
    finally:
        member_status.stop()
        if monitor_task is not None:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task


def create_app(*, session: WebSession, port: int) -> FastAPI:
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
    app.state.revisions = RevisionTracker()
    app.state.timeline_cache = TimelineCache()
    # lifespan 填 tmux/member_status；这里先占位默认值，ASGI 服务器(uvicorn)
    # 总会先跑 lifespan 再派发请求，但没有它们（比如构造 TestClient 时忘了
    # `with`）不该让 app.state.member_status 缺失属性冒 AttributeError→500，
    # 而是走 §2.4 的 503(见 API router 的 require_runtime 依赖)。
    app.state.tmux = None
    app.state.member_status = None
    app.state.lease_manager = None
    app.state.mirror_hub = MirrorHub()
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
        return HTMLResponse(_health_page())

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

    api = APIRouter(
        prefix=API_PREFIX,
        dependencies=[Depends(require_session), Depends(require_runtime)],
        default_response_class=ApiJSONResponse,
    )

    @api.get("/session")
    async def get_session() -> dict:
        return session_dto(app.state.revisions)

    @api.get("/bootstrap")
    async def get_bootstrap() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return bootstrap_dto(
            ctx, app.state.revisions, app.state.timeline_cache, app.state.member_status
        )

    @api.get("/vocabulary")
    async def get_vocabulary() -> dict:
        return vocabulary_dto(app.state.revisions)

    @api.get("/workspace")
    async def get_workspace() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return workspace_dto(ctx, app.state.revisions)

    @api.get("/team")
    async def get_team() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return team_dto(ctx, app.state.revisions)

    @api.get("/work")
    async def get_work() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return work_dto(ctx, app.state.revisions)

    @api.get("/work/tasks/{task_id}")
    async def get_task_detail(task_id: str) -> dict:
        ctx = build_context(tmux=app.state.tmux)
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
        ctx = build_context(tmux=app.state.tmux)
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
        ctx = build_context(tmux=app.state.tmux)
        text = timeline_body_text(ctx, app.state.timeline_cache, seq)
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment"},
        )

    @api.get("/members")
    async def get_members() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return members_dto(ctx, app.state.revisions, app.state.member_status)

    @api.get("/health")
    async def get_health() -> dict:
        ctx = build_context(tmux=app.state.tmux)
        return health_dto(ctx, app.state.revisions)

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

        async def reject(code: int, reason: str) -> None:
            await websocket.accept()
            await websocket.close(code=code, reason=reason)

        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin", "")
        if host not in hosts or origin not in origins:
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized")
            return
        if not session.verify_cookie(websocket.cookies.get(COOKIE_NAME)):
            await reject(WS_CLOSE_UNAUTHORIZED, "unauthorized")
            return

        ctx = build_context(tmux=app.state.tmux)
        if ctx.workspace is None or ctx.tmux is None or app.state.lease_manager is None:
            await reject(WS_CLOSE_UNAVAILABLE, "unavailable")
            return
        if member not in ctx.names:
            await reject(WS_CLOSE_NOT_FOUND, "member-not-found")
            return
        if not await asyncio.to_thread(ctx.tmux.has_session, member):
            await reject(WS_CLOSE_NOT_FOUND, "session-not-found")
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
            heartbeat_interval=app.state.heartbeat_interval,
        )
        with contextlib.suppress(WebSocketDisconnect):
            await run_mirror_connection(websocket, state)

    app.include_router(api)

    return app
