"""pane 崩溃检测与原地重启。"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tmuxctl.client import PaneInfo
from tmuxctl.errors import TmuxCommandError
from tmuxctl.process import is_missing_target_error

DEFAULT_CRASH_POLL_SECONDS = 0.25


class CrashKind(StrEnum):
    PANE_DIED = "pane-died"
    SESSION_MISSING = "session-missing"


@dataclass(frozen=True)
class CrashEvent:
    """检测到的一次 pane/会话消失。"""

    kind: CrashKind
    target: str
    pane_id: str | None
    detected_at: float
    exit_status: int | None = None


class LifecycleTarget(Protocol):
    """崩溃监视器需要的类型化 tmux 面。"""

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]: ...

    def display_message(self, target: str, format_string: str) -> str: ...

    def set_pane_remain_on_exit(self, target: str, enabled: bool = True) -> None: ...

    def set_hook(self, name: str, command: str) -> None: ...

    def unset_hook(self, name: str) -> None: ...

    def show_global_option(self, name: str) -> str | None: ...

    def unset_global_option(self, name: str) -> None: ...

    def respawn_pane(
        self,
        target: str,
        command: str | Sequence[str],
        *,
        kill: bool = True,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None: ...


class CrashMonitor:
    """hook 优先、轮询兜底的一次性崩溃等待器。"""

    def __init__(
        self,
        tmux: LifecycleTarget,
        *,
        poll_interval: float = DEFAULT_CRASH_POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 0 < poll_interval < 2:
            raise ValueError("poll_interval 必须在 0 到 2 秒之间")
        self._tmux = tmux
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleeper
        self.mode: str | None = None

    def _pane(self, target: str) -> PaneInfo | None:
        try:
            panes = self._tmux.list_panes(target)
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return None
            raise
        if not panes:
            return None
        if len(panes) > 1:
            raise ValueError(f"目标 {target} 匹配多个窗格,请传 pane id")
        return panes[0]

    def _probe(self, target: str, pane_id: str) -> CrashEvent | None:
        pane = self._pane(target)
        if pane is None or pane.pane_id != pane_id:
            return CrashEvent(CrashKind.SESSION_MISSING, target, pane_id, self._clock())
        try:
            raw = self._tmux.display_message(pane_id, "#{pane_dead}\t#{pane_dead_status}")
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return CrashEvent(CrashKind.SESSION_MISSING, target, pane_id, self._clock())
            raise
        dead, _, status = raw.strip().partition("\t")
        if dead != "1":
            return None
        exit_status = int(status) if status.strip().lstrip("-").isdigit() else None
        return CrashEvent(CrashKind.PANE_DIED, target, pane_id, self._clock(), exit_status)

    def _hook_event(
        self,
        target: str,
        pane: PaneInfo,
        option_name: str,
    ) -> CrashEvent | None:
        marker = self._tmux.show_global_option(option_name)
        if not marker:
            return None
        session, _, remainder = marker.partition("\t")
        pane_id, _, status = remainder.partition("\t")
        if session != pane.session_name or pane_id != pane.pane_id:
            return None
        exit_status = int(status) if status.strip().lstrip("-").isdigit() else None
        return CrashEvent(CrashKind.PANE_DIED, target, pane.pane_id, self._clock(), exit_status)

    async def wait(self, target: str, *, timeout: float | None = None) -> CrashEvent | None:
        """等待目标崩溃；超时返回 ``None``。"""
        pane = self._pane(target)
        if pane is None:
            return CrashEvent(CrashKind.SESSION_MISSING, target, None, self._clock())

        try:
            self._tmux.set_pane_remain_on_exit(pane.pane_id, True)
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return CrashEvent(CrashKind.SESSION_MISSING, target, pane.pane_id, self._clock())
            raise

        token = uuid.uuid4().hex
        hook_name = f"pane-died[{int(token[:6], 16)}]"
        option_name = f"@console_crash_{token}"
        marker = "#{session_name}\t#{pane_id}\t#{pane_dead_status}"
        command = f'set-option -g {option_name} "{marker}"'
        hook_installed = False
        try:
            try:
                self._tmux.set_hook(hook_name, command)
                hook_installed = True
                self.mode = "hook"
            except TmuxCommandError:
                self.mode = "poll"

            started = self._clock()
            while True:
                if hook_installed:
                    event = self._hook_event(target, pane, option_name)
                    if event is not None:
                        return event
                event = self._probe(target, pane.pane_id)
                if event is not None:
                    return event
                if timeout is not None and self._clock() - started >= timeout:
                    return None
                await self._sleep(self._poll_interval)
        finally:
            if hook_installed:
                with contextlib.suppress(TmuxCommandError):
                    self._tmux.unset_hook(hook_name)
                with contextlib.suppress(TmuxCommandError):
                    self._tmux.unset_global_option(option_name)

    def respawn(
        self,
        target: str,
        command: str | Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """在原 pane 强制重启成员命令。"""
        self._tmux.respawn_pane(target, command, kill=True, cwd=cwd, env=env)
