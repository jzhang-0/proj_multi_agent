"""发现并收编不在静态名册里的现有 tmux 会话。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from roster.schema import Roster, RosterError, validate_member_name
from tmuxctl import PaneInfo, TmuxCommandError, is_missing_target_error


class SessionSource(Protocol):
    """会话收编只依赖的 tmux 最小接口。"""

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]: ...

    def has_session(self, name: str) -> bool: ...


@dataclass(frozen=True)
class SessionCandidate:
    """一个可收编的 tmux 会话快照。"""

    name: str
    pane_ids: tuple[str, ...]
    commands: tuple[str, ...]


@dataclass(frozen=True)
class TemporaryMember:
    """本进程内收编的成员；不会写回 ``roster.toml``。"""

    name: str
    pane_ids: tuple[str, ...]
    commands: tuple[str, ...]
    temporary: bool = True


class SessionAdopter:
    """维护静态名册之外、本进程内有效的临时成员。"""

    def __init__(self, roster: Roster, tmux: SessionSource) -> None:
        self.roster = roster
        self.tmux = tmux
        self._adopted: dict[str, TemporaryMember] = {}

    def _session_candidates(self) -> dict[str, SessionCandidate]:
        try:
            panes = self.tmux.list_panes(all_sessions=True)
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return {}
            raise

        grouped: dict[str, list[PaneInfo]] = {}
        for pane in panes:
            grouped.setdefault(pane.session_name, []).append(pane)

        candidates: dict[str, SessionCandidate] = {}
        for name, session_panes in grouped.items():
            try:
                validate_member_name(name)
            except RosterError:
                continue
            ordered = sorted(session_panes, key=lambda pane: (pane.window_index, pane.pane_index))
            candidates[name] = SessionCandidate(
                name=name,
                pane_ids=tuple(pane.pane_id for pane in ordered),
                commands=tuple(pane.current_command for pane in ordered),
            )
        return candidates

    def discover(self) -> tuple[SessionCandidate, ...]:
        """列出不在静态名册且尚未收编的、名称合法的现有会话。"""
        configured = {member.name for member in self.roster.members}
        candidates = self._session_candidates()
        return tuple(
            candidates[name]
            for name in sorted(candidates)
            if name not in configured and name not in self._adopted
        )

    def adopt(self, name: str) -> TemporaryMember:
        """把一个现有会话收编为临时成员；重复调用幂等。"""
        validate_member_name(name)
        configured = self.roster.get(name)
        if configured is not None:
            raise RosterError(f"成员 {name!r} 已在 roster.toml 中,无需收编")
        existing = self._adopted.get(name)
        if existing is not None:
            return existing

        candidate = self._session_candidates().get(name)
        if candidate is None:
            raise RosterError(f"找不到可收编的 tmux 会话: {name}")
        adopted = TemporaryMember(candidate.name, candidate.pane_ids, candidate.commands)
        self._adopted[name] = adopted
        return adopted

    def forget(self, name: str) -> bool:
        """移除临时成员记录，不关闭对应 tmux 会话。"""
        return self._adopted.pop(name, None) is not None

    def adopted_members(self) -> tuple[TemporaryMember, ...]:
        return tuple(self._adopted.values())

    def member_names(self) -> tuple[str, ...]:
        """统一成员名列表，供收件人补全、成员栏与时间线着色使用。"""
        configured = tuple(member.name for member in self.roster.enabled_members())
        return configured + tuple(self._adopted)

    def is_member(self, name: str) -> bool:
        """名称是否属于启用的静态成员或已收编的临时成员。"""
        return name in self.member_names()

    def can_receive(self, name: str) -> bool:
        """成员当前是否有可接收总线注入的同名 tmux 会话。"""
        return self.is_member(name) and self.tmux.has_session(name)
