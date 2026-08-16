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


def _split(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        return _split(value)
    return ()


def _workspace_gates(value: object) -> tuple[WorkspaceGate, ...]:
    if not isinstance(value, dict):
        return ()
    items: list[WorkspaceGate] = []
    for slug, spec in value.items():
        users: tuple[str, ...] = ()
        rooms: tuple[str, ...] = ()
        if isinstance(spec, dict):
            users = _str_tuple(spec.get("users"))
            rooms = _str_tuple(spec.get("rooms"))
        items.append(WorkspaceGate(slug=str(slug), users=users, rooms=rooms))
    return tuple(items)


def _workspaces_toml(items: tuple[WorkspaceGate, ...]) -> str:
    if not items:
        return ""
    lines = []
    for item in items:
        users = ", ".join(f'"{name}"' for name in item.users)
        rooms = ", ".join(f'"{name}"' for name in item.rooms)
        lines.append(f"\n[workspaces.{item.slug}]")
        lines.append(f"users = [{users}]")
        lines.append(f"rooms = [{rooms}]")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class WorkspaceGate:
    """一个工作区自己的网关白名单。`users`/`rooms` 空表示沿用全局。"""

    slug: str
    users: tuple[str, ...] = ()
    rooms: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewayConfig:
    """自建网关的运行参数。"""

    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    token: str = ""
    room: str = "default"
    #: 白名单(GATE-004)。`users` 为空表示还没配,网关一律拒绝服务
    users: tuple[str, ...] = ()
    rooms: tuple[str, ...] = ()
    #: 按工作区覆盖的白名单(WS-008)。没有表就用上面的全局名单
    workspaces: tuple[WorkspaceGate, ...] = ()

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
            users=_str_tuple(raw.get("users")),
            rooms=_str_tuple(raw.get("rooms")),
            workspaces=_workspace_gates(raw.get("workspaces")),
        )
        return config.with_env()

    def workspace_spec(self, slug: str) -> WorkspaceGate | None:
        for item in self.workspaces:
            if item.slug == slug:
                return item
        return None

    def with_env(self) -> GatewayConfig:
        """环境变量覆盖文件里的值(临时改端口、换 token 时不用动文件)。"""
        env_users = os.environ.get("GATEWAY_USERS")
        return replace(
            self,
            host=os.environ.get("GATEWAY_HOST", self.host),
            port=int(os.environ.get("GATEWAY_PORT", self.port)),
            token=os.environ.get("GATEWAY_TOKEN", self.token),
            room=os.environ.get("GATEWAY_ROOM", self.room),
            users=_split(env_users) if env_users is not None else self.users,
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
        users = ", ".join(f'"{name}"' for name in self.users)
        rooms = ", ".join(f'"{name}"' for name in self.rooms)
        return (
            "# 自建 IM 网关配置。token 是访问口令,别提交、别贴群里。\n"
            "# users 是白名单:为空时网关谁都不服务(GATE-004)。\n"
            "# [workspaces.<slug>] 可为每个工作区单独写 users / rooms(WS-008)。\n"
            f'host = "{self.host}"\n'
            f"port = {self.port}\n"
            f'token = "{self.token}"\n'
            f'room = "{self.room}"\n'
            f"users = [{users}]\n"
            f"rooms = [{rooms}]\n"
            f"{_workspaces_toml(self.workspaces)}"
        )

    def url_for(self, address: str) -> str:
        return f"http://{address}:{self.port}/?token={self.token}"
