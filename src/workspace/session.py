"""成员短名 ↔ tmux 会话名。格式 `<成员>@<slug>`。

这是唯一允许做这件翻译的模块。tmuxctl 继续吃真实会话名;roster / hub /
console 把成员短名交给 `bind_tmux()` 包过的客户端,由这里统一拼接。
`human`、`bus`、`im:*` 没有 tmux 会话,不参与拼接。

未登记工作区时 slug 为空,映射是恒等(短名=会话名),单工作区旧入口先不改名。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from bus.message import REMOTE_PREFIX
from tmuxctl import PaneInfo, Tmux
from workspace.errors import WorkspaceError
from workspace.resolve import resolve_from_cwd
from workspace.slug import validate_slug
from workspace.store import Store

_PANE_PREFIX = "%"
_SESSIONLESS = frozenset({"human", "bus"})


class SessionNameError(WorkspaceError):
    """成员名不能拼进 tmux 会话名,或保留名被拿去拼 slug。"""


def is_sessionless(name: str) -> bool:
    """这些名字没有 tmux 会话:`human` / `bus` / `im:*`。"""
    return name in _SESSIONLESS or name.startswith(REMOTE_PREFIX)


@dataclass(frozen=True)
class SessionNames:
    """一组工作区里的成员名 ↔ 会话名对照。`slug is None` 表示恒等映射。"""

    slug: str | None = None

    @classmethod
    def identity(cls) -> SessionNames:
        return cls(slug=None)

    @classmethod
    def from_cwd(
        cls,
        cwd: str | Path | None = None,
        *,
        store: Store | None = None,
    ) -> SessionNames:
        """当前目录属于已登记工作区就用它的 slug,否则恒等。"""
        workspace = resolve_from_cwd(cwd, store=store)
        if workspace is None:
            return cls.identity()
        return cls(slug=workspace.slug)

    @property
    def is_identity(self) -> bool:
        return self.slug is None

    def session_of(self, member: str) -> str:
        """短名 → tmux 会话名。已经带本工作区后缀的原样返回,避免二次拼接。"""
        if is_sessionless(member):
            raise SessionNameError(f"{member!r} 是保留名,没有 tmux 会话,不能拼命名空间")
        if ":" in member:
            raise SessionNameError(
                f"成员名 {member!r} 含非法字符 ':'(tmux 会把它当成 session:window 分隔符)"
            )
        if "." in member:
            raise SessionNameError(
                f"成员名 {member!r} 含非法字符 '.'(tmux 会把它静默改写成 '_')"
            )
        if self.slug is None:
            return member
        validate_slug(self.slug)
        suffix = f"@{self.slug}"
        if member.endswith(suffix) and len(member) > len(suffix):
            return member
        return f"{member}@{self.slug}"

    def member_of(self, session: str) -> str:
        """tmux 会话名 → 短名。不属于本工作区的原样返回。"""
        if self.slug is None or is_sessionless(session):
            return session
        suffix = f"@{self.slug}"
        if session.endswith(suffix) and len(session) > len(suffix):
            return session[: -len(suffix)]
        return session

    def owns(self, session: str) -> bool:
        """这个 tmux 会话是不是本工作区的成员会话。"""
        if is_sessionless(session):
            return False
        if self.slug is None:
            return "@" not in session
        suffix = f"@{self.slug}"
        return session.endswith(suffix) and len(session) > len(suffix)


def session_for(holder: object, member: str) -> str:
    """从绑了 `session_names` 的 tmux 客户端(或 SessionNames 自身)翻译短名。"""
    if isinstance(holder, SessionNames):
        return holder.session_of(member)
    names = getattr(holder, "session_names", None)
    if isinstance(names, SessionNames):
        return names.session_of(member)
    return member


def member_for(holder: object, session: str) -> str:
    """反向:`claude@demo` → `claude`。没有绑定就原样返回。"""
    if isinstance(holder, SessionNames):
        return holder.member_of(session)
    names = getattr(holder, "session_names", None)
    if isinstance(names, SessionNames):
        return names.member_of(session)
    return session


class NamespacedTmux:
    """对 Tmux 的薄封装:成员短名进,真实会话名出。pane id(`%N`)不翻译。"""

    def __init__(self, tmux: Tmux, names: SessionNames) -> None:
        self._inner = tmux
        self.session_names = names

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _map(self, target: str) -> str:
        if target.startswith(_PANE_PREFIX):
            return target
        return self.session_names.session_of(target)

    def has_session(self, name: str) -> bool:
        return self._inner.has_session(self._map(name))

    def new_session(
        self,
        name: str,
        *,
        command: str | Sequence[str] | None = None,
        detached: bool = True,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        window_name: str | None = None,
    ) -> None:
        self._inner.new_session(
            self._map(name),
            command=command,
            detached=detached,
            cwd=cwd,
            env=env,
            window_name=window_name,
        )

    def kill_session(self, name: str, *, missing_ok: bool = False) -> None:
        self._inner.kill_session(self._map(name), missing_ok=missing_ok)

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None:
        self._inner.send_keys(self._map(target), *keys, literal=literal)

    def send_line(self, target: str, text: str) -> None:
        self._inner.send_line(self._map(target), text)

    def capture_with_cursor(self, target: str, *, escape: bool = False) -> tuple[str, int]:
        return self._inner.capture_with_cursor(self._map(target), escape=escape)

    def capture_pane(
        self,
        target: str,
        *,
        escape: bool = False,
        start: int | str | None = None,
        end: int | str | None = None,
    ) -> str:
        return self._inner.capture_pane(
            self._map(target), escape=escape, start=start, end=end
        )

    def fit_window(self, target: str, width: int, height: int) -> None:
        self._inner.fit_window(self._map(target), width, height)

    def release_window_size(self, target: str) -> None:
        self._inner.release_window_size(self._map(target))

    def display_message(self, target: str, format_string: str) -> str:
        return self._inner.display_message(self._map(target), format_string)

    def set_pane_remain_on_exit(self, target: str, enabled: bool = True) -> None:
        self._inner.set_pane_remain_on_exit(self._map(target), enabled)

    def respawn_pane(
        self,
        target: str,
        command: str | Sequence[str],
        *,
        kill: bool = True,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._inner.respawn_pane(
            self._map(target), command, kill=kill, cwd=cwd, env=env
        )

    def pipe_pane(self, target: str, command: str | None = None) -> None:
        self._inner.pipe_pane(self._map(target), command)

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]:
        mapped = None if target is None or all_sessions else self._map(target)
        return self._inner.list_panes(mapped, all_sessions=all_sessions)


def bind_tmux(
    tmux: Tmux | None = None,
    names: SessionNames | None = None,
) -> NamespacedTmux:
    """生产路径上拿 tmux 客户端的唯一入口:自动带上当前工作区的命名空间。"""
    inner = tmux if tmux is not None else Tmux()
    bound = names if names is not None else SessionNames.from_cwd()
    return NamespacedTmux(inner, bound)
