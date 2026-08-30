"""统一错误模型(架构 §2.4):账本损坏、鉴权失败等一律映射成结构化 JSON，不出 500。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiJSONResponse(JSONResponse):
    """§2.1 要求响应带 `charset=utf-8`；Starlette 默认只给 `application/json`。

    错误响应(§2.4)也是响应，同样受 §2.1 约束——统一在这里定义，供
    `register_error_handlers` 与 `web.app` 里手写的 401(Host 校验)复用，
    避免两处各自漏加 charset。
    """

    media_type = "application/json; charset=utf-8"


class ApiError(Exception):
    """携带 §2.4 错误码的可预期失败；由统一 handler 转成响应，不冒泡成 500。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        domain: str = "web",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.domain = domain


def _payload(exc: ApiError) -> dict[str, dict[str, str]]:
    return {"error": {"code": exc.code, "message": exc.message, "domain": exc.domain}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> ApiJSONResponse:
        return ApiJSONResponse(status_code=exc.status_code, content=_payload(exc))
