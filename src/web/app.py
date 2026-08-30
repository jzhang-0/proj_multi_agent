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
import mimetypes
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from control.health import HealthMonitor
from control.members import MemberStatusService
from web.actions import (
    download_attachment,
    read_image_body,
    read_message_payload,
    send_message,
    upload_attachment,
)
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
from web.stream import EventHub, StreamSettings, handle_stream

API_PREFIX = "/api/v1"


def allowed_hosts(port: int) -> frozenset[str]:
    """`Host` 头白名单(架构 §6.3):只接受本机地址 + 当前监听端口。"""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


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
    member_status = MemberStatusService(ctx.names, ctx.tmux)
    app.state.member_status = member_status
    health: HealthMonitor | None = None
    if ctx.paths is not None:
        health = HealthMonitor(ctx.paths, ctx.names, ctx.tmux, interval=settings.health_interval_s)
    app.state.health_monitor = health
    hub = EventHub(
        tracker=app.state.revisions,
        cache=app.state.timeline_cache,
        member_status=member_status,
        health=health,
        tmux=ctx.tmux,
        settings=settings,
    )
    app.state.stream = hub
    tasks: list[asyncio.Task[None]] = []
    if member_status.can_monitor:
        tasks.append(asyncio.create_task(member_status.run(), name="web-member-status"))
    else:
        for name in ctx.names:
            member_status.set_alive(name, False)
    tasks.append(asyncio.create_task(hub.run(), name="web-event-hub"))
    try:
        yield
    finally:
        hub.stop()
        member_status.stop()
        if health is not None:
            health.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


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
    app.state.stream_settings = stream_settings or StreamSettings()
    app.state.revisions = RevisionTracker()
    app.state.timeline_cache = TimelineCache()
    # lifespan 填 tmux/member_status/stream；这里先占位默认值，ASGI 服务器
    # 总会先跑 lifespan 再派发请求，但没有它们（比如构造 TestClient 时忘了
    # `with`）不该让 app.state.member_status 缺失属性冒 AttributeError→500，
    # 而是走 §2.4 的 503(见 API router 的 require_runtime 依赖)。
    app.state.tmux = None
    app.state.member_status = None
    app.state.health_monitor = None
    app.state.stream = None
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
        ctx = build_context(tmux=app.state.tmux)
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
        ctx = build_context(tmux=app.state.tmux)
        payload = await read_message_payload(request)
        return send_message(ctx, actor=session.actor, payload=payload)

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
        """支持刷新 `/workspace`、`/task/<id>`、`/help` 等前端路由。"""
        require_session(request)
        if spa_path in {"workspace", "timeline", "help"} or spa_path.startswith("task/"):
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
