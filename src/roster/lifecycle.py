"""成员生命周期:`up` / `down` / `restart`,单个或全体,全部幂等。

幂等的含义是"看结果不看动作":`up` 保证成员在跑(已经在跑就不碰它,
绝不重复拉起——重复拉起会把一个正在干活的 CLI 顶掉),`down` 保证它不
在跑,`restart` 保证它是新起的一份。每个动作回一条 `LifecycleResult`,
`changed` 说明这次到底动没动它,便于总控台只对真正的变化做提示。

启动细节(`AGENT_NAME` 注入、开场白)在 `roster.start`,这里只管编排。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from roster.schema import Member, Roster, RosterError
from roster.start import start_member, stop_member
from tmuxctl import Tmux
from workspace.session import session_for


class Action(StrEnum):
    UP = "up"
    DOWN = "down"
    RESTART = "restart"


@dataclass(frozen=True)
class LifecycleResult:
    """一个成员在一次动作里的结果。"""

    name: str
    action: Action
    changed: bool
    detail: str

    def line(self) -> str:
        """打印用的一行。"""
        return f"[{self.action}] {self.name} {self.detail}"


class Lifecycle:
    """把名册和 tmux 兜在一起的生命周期 API。"""

    def __init__(self, roster: Roster, tmux: Tmux, *, cwd: Path | None = None) -> None:
        self.roster = roster
        self.tmux = tmux
        self.cwd = cwd

    # --- 选成员 ---------------------------------------------------------

    def _one(self, name: str) -> Member:
        member = self.roster.get(name)
        if member is None:
            raise RosterError(f"未知成员: {name}")
        return member

    def _targets(self, name: str | None, *, only_enabled: bool) -> tuple[Member, ...]:
        if name is not None:
            return (self._one(name),)
        if only_enabled:
            return self.roster.enabled_members()
        return self.roster.members

    # --- 单个成员 -------------------------------------------------------

    def up_member(self, member: Member) -> LifecycleResult:
        """保证成员在跑。已在跑或已停用都不动它。"""
        if not member.enabled:
            return LifecycleResult(member.name, Action.UP, False, "已停用,跳过")
        if self.tmux.has_session(member.name):
            return LifecycleResult(member.name, Action.UP, False, "已在运行,跳过")
        start_member(member, self.tmux, cwd=self.cwd)
        session = session_for(self.tmux, member.name)
        return LifecycleResult(
            member.name,
            Action.UP,
            True,
            f"已启动 (查看: tmux attach -t {session})",
        )

    def down_member(self, member: Member) -> LifecycleResult:
        """保证成员不在跑。本来就没在跑就什么都不做。"""
        if not self.tmux.has_session(member.name):
            return LifecycleResult(member.name, Action.DOWN, False, "未运行")
        stop_member(member, self.tmux)
        return LifecycleResult(member.name, Action.DOWN, True, "已关闭")

    def restart_member(self, member: Member) -> LifecycleResult:
        """保证成员是新起的一份:在跑的先关掉再拉起,没在跑的直接拉起。"""
        if not member.enabled:
            return LifecycleResult(member.name, Action.RESTART, False, "已停用,跳过")
        was_running = self.tmux.has_session(member.name)
        if was_running:
            stop_member(member, self.tmux)
        start_member(member, self.tmux, cwd=self.cwd)
        detail = "已重启" if was_running else "原来没在跑,已拉起"
        return LifecycleResult(member.name, Action.RESTART, True, detail)

    # --- 单个或全体 -----------------------------------------------------

    def up(self, name: str | None = None) -> list[LifecycleResult]:
        """拉起指定成员;不给名字就是名册里全部启用的成员。"""
        return [self.up_member(m) for m in self._targets(name, only_enabled=True)]

    def down(self, name: str | None = None) -> list[LifecycleResult]:
        """关掉指定成员;不给名字就是名册里全部成员(含已停用的,防残留会话)。"""
        return [self.down_member(m) for m in self._targets(name, only_enabled=False)]

    def restart(self, name: str | None = None) -> list[LifecycleResult]:
        """重启指定成员;不给名字就是名册里全部启用的成员。"""
        return [self.restart_member(m) for m in self._targets(name, only_enabled=True)]

    def running(self) -> tuple[str, ...]:
        """当前真的有 tmux 会话在跑的成员名。"""
        return tuple(m.name for m in self.roster.members if self.tmux.has_session(m.name))


def render(results: Iterable[LifecycleResult]) -> str:
    return "\n".join(result.line() for result in results)
