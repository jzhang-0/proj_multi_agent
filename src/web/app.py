"""FastAPI 应用工厂(WEB-003 地基):本机认证会话 + 最小静态健康页。

只读 snapshot(workspace/team/work/timeline/member)依赖 WEB-001 下沉的
控制面 DTO,尚未接入——这里先把"源码仓库外安装、无 Node、无 CDN 也能起
一个通过鉴权的 Web 服务"这条约束落地,snapshot 端点等前置合入后再接进
同一个 `create_app()`。
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from web.auth import COOKIE_NAME, WebSession


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

    @app.middleware("http")
    async def _check_host(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        host = request.headers.get("host", "")
        if host not in hosts:
            return _error(
                "unauthorized",
                f"不接受的 Host: {host!r}(防 DNS rebinding，见架构 §6.3)",
                status_code=401,
            )
        return await call_next(request)

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

    return app
