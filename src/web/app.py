"""FastAPI 应用工厂(WEB-003):本机认证会话 + 只读 snapshot API + 最小静态健康页。

`/api/v1/*` 只读、无副作用，只经控制面(`control/`)调用领域层；不建立第二套
状态库，不触发 tmux resize/send_keys(架构 §1)。鉴权、Host 校验、
Cache-Control 头统一在这里处理，DTO 组装在 `web.snapshots`。
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from web.auth import COOKIE_NAME, WebSession
from web.context import build_context
from web.errors import ApiError, register_error_handlers
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

API_PREFIX = "/api/v1"


class ApiJSONResponse(JSONResponse):
    """§2.1 要求响应带 `charset=utf-8`；Starlette 默认只给 `application/json`。"""

    media_type = "application/json; charset=utf-8"


def allowed_hosts(port: int) -> frozenset[str]:
    """`Host` 头白名单(架构 §6.3):只接受本机地址 + 当前监听端口。"""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def _error(code: str, message: str, *, status_code: int, domain: str = "web") -> JSONResponse:
    """§2.4 统一错误模型。"""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "domain": domain}},
    )


def _health_page() -> str:
    resource = importlib.resources.files("web").joinpath("static", "health.html")
    return resource.read_text(encoding="utf-8")


def create_app(*, session: WebSession, port: int) -> FastAPI:
    """构造应用;`session` 与 `port` 由 `web.cli.main` 每次启动时生成/确定。

    不注册 `CORSMiddleware`(架构 §6.3 明确要求不设任何 CORS 响应头);
    也关掉自带的 `/docs`、`/redoc`、`/openapi.json`,减少未鉴权即可访问
    的面。
    """
    app = FastAPI(title="amux web", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = allowed_hosts(port)
    app.state.revisions = RevisionTracker()
    app.state.timeline_cache = TimelineCache()
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

    api = APIRouter(
        prefix=API_PREFIX,
        dependencies=[Depends(require_session)],
        default_response_class=ApiJSONResponse,
    )

    @api.get("/session")
    async def get_session() -> dict:
        return session_dto(app.state.revisions)

    @api.get("/bootstrap")
    async def get_bootstrap() -> dict:
        ctx = build_context()
        return bootstrap_dto(ctx, app.state.revisions, app.state.timeline_cache)

    @api.get("/vocabulary")
    async def get_vocabulary() -> dict:
        return vocabulary_dto(app.state.revisions)

    @api.get("/workspace")
    async def get_workspace() -> dict:
        ctx = build_context()
        return workspace_dto(ctx, app.state.revisions)

    @api.get("/team")
    async def get_team() -> dict:
        ctx = build_context()
        return team_dto(ctx, app.state.revisions)

    @api.get("/work")
    async def get_work() -> dict:
        ctx = build_context()
        return work_dto(ctx, app.state.revisions)

    @api.get("/work/tasks/{task_id}")
    async def get_task_detail(task_id: str) -> dict:
        ctx = build_context()
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
        ctx = build_context()
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
        ctx = build_context()
        text = timeline_body_text(ctx, app.state.timeline_cache, seq)
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment"},
        )

    @api.get("/members")
    async def get_members() -> dict:
        ctx = build_context()
        return members_dto(ctx, app.state.revisions)

    @api.get("/health")
    async def get_health() -> dict:
        ctx = build_context()
        return health_dto(ctx, app.state.revisions)

    app.include_router(api)

    return app
