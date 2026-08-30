"""本机 Web 会话认证(WEB-003 §6):进程内 token 换 HttpOnly cookie。

`amux web` 启动时生成一次性 `token`,只打印在终端、只留在进程内存;浏览器
带 `?token=` 首次访问后换成会话 cookie,之后所有请求只认 cookie。进程
退出即两者一起失效——不落盘,也不像 `gateway.toml` 那样跨进程复用,因为
这不是给手机反复扫码用的长期凭证,持久化只会多一个泄露面。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

#: 浏览器换到的会话 cookie 名。
COOKIE_NAME = "amux_session"


@dataclass(frozen=True)
class WebSession:
    """一次 `amux web` 进程生命周期内唯一的认证状态。"""

    token: str = field(repr=False)
    session_id: str = field(repr=False)

    @classmethod
    def generate(cls) -> WebSession:
        return cls(token=secrets.token_urlsafe(32), session_id=secrets.token_urlsafe(32))

    def verify_token(self, candidate: str | None) -> bool:
        """常数时间比较,防止用响应耗时猜 token。"""
        return candidate is not None and secrets.compare_digest(candidate, self.token)

    def verify_cookie(self, candidate: str | None) -> bool:
        return candidate is not None and secrets.compare_digest(candidate, self.session_id)
