"""工作区/成员并发上限:可配,超限只告警不拒绝(2026-08-17 拍板)。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from workspace.paths import amux_home
from workspace.session import is_sessionless
from workspace.store import Store

LIMITS_NAME = "limits.toml"
DEFAULT_WARN_WORKSPACES = 8
DEFAULT_WARN_MEMBERS = 16


@dataclass(frozen=True)
class Limits:
    """建议上限。0 表示不告警。"""

    warn_workspaces: int = DEFAULT_WARN_WORKSPACES
    warn_members: int = DEFAULT_WARN_MEMBERS

    @classmethod
    def load(cls, home: str | Path | None = None) -> Limits:
        root = Path(home).expanduser().resolve() if home is not None else amux_home()
        target = root / LIMITS_NAME
        if not target.is_file():
            return cls()
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
        return cls(
            warn_workspaces=_int(raw.get("warn_workspaces"), DEFAULT_WARN_WORKSPACES),
            warn_members=_int(raw.get("warn_members"), DEFAULT_WARN_MEMBERS),
        )


def _int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def warn_workspace_count(store: Store, *, extra: int = 0, limits: Limits | None = None) -> str:
    """工作区个数(含即将加上的)超过建议值时返回告警,否则空串。"""
    cap = (limits or Limits.load(store.home)).warn_workspaces
    if cap <= 0:
        return ""
    count = len(store.list()) + extra
    if count <= cap:
        return ""
    return (
        f"告警:已有 {count} 个工作区,超过建议上限 {cap}"
        f"(不拒绝;可在 {store.home / LIMITS_NAME} 改 warn_workspaces)"
    )


def namespaced_sessions(tmux: object) -> tuple[str, ...]:
    """当前 tmux 里带 `@slug` 的成员会话(真实会话名,不翻译)。"""
    list_panes = getattr(tmux, "list_panes", None)
    if list_panes is None:
        return ()
    inner = getattr(tmux, "_inner", tmux)
    list_panes = getattr(inner, "list_panes", list_panes)
    try:
        panes = list_panes(all_sessions=True)
    except Exception:
        return ()
    seen: list[str] = []
    for pane in panes:
        name = getattr(pane, "session_name", None)
        if not isinstance(name, str) or name in seen:
            continue
        if "@" not in name or is_sessionless(name):
            continue
        seen.append(name)
    return tuple(seen)


def warn_member_count(tmux: object, *, extra: int = 0, limits: Limits | None = None) -> str:
    """成员会话数(含即将拉起的)超过建议值时返回告警,否则空串。"""
    cap = (limits or Limits.load()).warn_members
    if cap <= 0:
        return ""
    count = len(namespaced_sessions(tmux)) + extra
    if count <= cap:
        return ""
    home = amux_home()
    return (
        f"告警:已有 {count} 个成员会话,超过建议上限 {cap}"
        f"(不拒绝;可在 {home / LIMITS_NAME} 改 warn_members)"
    )
