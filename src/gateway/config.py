"""网关配置与凭证。

自建网关没有第三方账号,但**不能没有凭证**:它监听在局域网上,同一个
WiFi 下的任何设备都能访问。所以有一个 token,页面和接口都要带。

来源优先级:显式参数 > 环境变量(`GATEWAY_TOKEN` / `GATEWAY_PORT` /
`GATEWAY_HOST` / `GATEWAY_ROOM`)> 仓库根 `gateway.toml`。第一次跑没有
token 就随机生成一个并写进 `gateway.toml`(该文件已 gitignore,别提交)。
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from bus.paths import repo_root

#: 配置文件名(仓库根)
CONFIG_FILENAME = "gateway.toml"

#: 默认端口。挑一个不常被占的高位端口
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class GatewayConfig:
    """自建网关的运行参数。"""

    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    token: str = ""
    room: str = "default"

    @classmethod
    def load(cls, path: Path | None = None) -> GatewayConfig:
        target = path or (repo_root() / CONFIG_FILENAME)
        raw: dict[str, object] = {}
        if target.is_file():
            raw = tomllib.loads(target.read_text(encoding="utf-8"))
        config = cls(
            host=str(raw.get("host", cls.host)),
            port=int(raw.get("port", cls.port)),  # type: ignore[arg-type]
            token=str(raw.get("token", "")),
            room=str(raw.get("room", cls.room)),
        )
        return config.with_env()

    def with_env(self) -> GatewayConfig:
        """环境变量覆盖文件里的值(临时改端口、换 token 时不用动文件)。"""
        return replace(
            self,
            host=os.environ.get("GATEWAY_HOST", self.host),
            port=int(os.environ.get("GATEWAY_PORT", self.port)),
            token=os.environ.get("GATEWAY_TOKEN", self.token),
            room=os.environ.get("GATEWAY_ROOM", self.room),
        )

    def ensure_token(self, path: Path | None = None) -> GatewayConfig:
        """没有 token 就随机生成一个并落盘,返回带 token 的配置。"""
        if self.token:
            return self
        target = path or (repo_root() / CONFIG_FILENAME)
        config = replace(self, token=secrets.token_urlsafe(12))
        target.write_text(config.as_toml(), encoding="utf-8")
        return config

    def as_toml(self) -> str:
        return (
            "# 自建 IM 网关配置。token 是访问口令,别提交、别贴群里。\n"
            f'host = "{self.host}"\n'
            f"port = {self.port}\n"
            f'token = "{self.token}"\n'
            f'room = "{self.room}"\n'
        )

    def url_for(self, address: str) -> str:
        return f"http://{address}:{self.port}/?token={self.token}"
