"""跨前端的 tmux、成员会话与 bus 健康事件。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from bus import BusPaths
from bus.sanitize import sanitize
from tmuxctl import PaneInfo, TmuxCommandError
from workspace.session import session_for


class FaultKind(StrEnum):
    TMUX_SERVER = "tmux-server"
    MEMBER_SESSION = "member-session"
    BUS_UNWRITABLE = "bus-unwritable"


@dataclass(frozen=True)
class Fault:
    kind: FaultKind
    target: str
    detail: str
    key: str = field(init=False)

    def __post_init__(self) -> None:
        target = sanitize(self.target)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "detail", sanitize(self.detail))
        object.__setattr__(self, "key", f"{self.kind}:{target}")


@dataclass(frozen=True)
class FaultEvent:
    fault: Fault
    recovered: bool = False


class HealthTmux(Protocol):
    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]: ...


WriteProbe = Callable[[Path], None]


def probe_writable(directory: Path) -> None:
    target = directory / f".console-health-{uuid.uuid4().hex}"
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write("ok")
    finally:
        target.unlink(missing_ok=True)


class HealthMonitor:
    """轮询外部依赖，并把状态变化投影成可序列化边沿事件。"""

    def __init__(
        self,
        paths: BusPaths,
        names: tuple[str, ...],
        tmux: HealthTmux | None,
        *,
        interval: float = 0.5,
        write_probe: WriteProbe = probe_writable,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval 必须大于 0")
        self.paths = paths
        self.names = names
        self.tmux = tmux
        self.interval = interval
        self._write_probe = write_probe
        self._active: dict[str, Fault] = {}
        self._stopped = False

    def track(self, names: tuple[str, ...]) -> None:
        self.names = names

    @property
    def active(self) -> dict[str, Fault]:
        """最近一次 `update()` 后仍活跃的故障集；snapshot 读这个，不要再 `update()`。"""
        return dict(self._active)

    def probe(self) -> dict[str, Fault]:
        faults: dict[str, Fault] = {}
        try:
            self._write_probe(self.paths.queue)
        except Exception as exc:
            fault = Fault(
                FaultKind.BUS_UNWRITABLE,
                str(self.paths.queue),
                f"{type(exc).__name__}: {exc}",
            )
            faults[fault.key] = fault

        if self.tmux is None:
            fault = Fault(FaultKind.TMUX_SERVER, "tmux", "tmux 不可用")
            faults[fault.key] = fault
            return faults

        try:
            panes = self.tmux.list_panes(all_sessions=True)
        except TmuxCommandError as exc:
            fault = Fault(FaultKind.TMUX_SERVER, "tmux", str(exc).splitlines()[-1])
            faults[fault.key] = fault
            return faults
        except Exception as exc:
            fault = Fault(FaultKind.TMUX_SERVER, "tmux", f"{type(exc).__name__}: {exc}")
            faults[fault.key] = fault
            return faults

        sessions = {pane.session_name for pane in panes}
        for name in self.names:
            if session_for(self.tmux, name) not in sessions:
                fault = Fault(FaultKind.MEMBER_SESSION, name, "成员 tmux 会话消失")
                faults[fault.key] = fault
        return faults

    def update(self, current: dict[str, Fault]) -> list[FaultEvent]:
        events = [
            FaultEvent(fault)
            for key, fault in current.items()
            if key not in self._active
        ]
        events.extend(
            FaultEvent(fault, recovered=True)
            for key, fault in self._active.items()
            if key not in current
        )
        self._active = current
        return events

    async def run(self, on_event: Callable[[FaultEvent], None]) -> None:
        self._stopped = False
        while not self._stopped:
            current = await asyncio.to_thread(self.probe)
            for event in self.update(current):
                on_event(event)
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self._stopped = True
