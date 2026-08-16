"""名册成员健康监督与可配置自动拉起。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from roster.schema import Member, Roster, RosterError
from roster.start import member_env, start_member, window_command
from tmuxctl import CrashEvent, CrashKind, CrashMonitor, Tmux
from workspace.resolve import project_root_for_members

MAX_CONSECUTIVE_FAILURES = 3


class HealthState(StrEnum):
    """健康监督器对外暴露的成员状态。"""

    RUNNING = "running"
    DEAD = "dead"
    FAILED = "failed"


@dataclass(frozen=True)
class HealthUpdate:
    """一次健康状态变化，可直接交给 console 告警区。"""

    name: str
    state: HealthState
    consecutive_failures: int
    detail: str


MonitorFactory = Callable[[Tmux], CrashMonitor]
AlertSink = Callable[[HealthUpdate], None]


class HealthSupervisor:
    """监听成员崩溃，并按成员配置决定告警或自动恢复。"""

    def __init__(
        self,
        roster: Roster,
        tmux: Tmux,
        *,
        cwd: Path | None = None,
        retry_delay: float = 1.0,
        monitor_factory: MonitorFactory = CrashMonitor,
        on_update: AlertSink | None = None,
    ) -> None:
        if retry_delay < 0:
            raise ValueError("retry_delay 不能为负数")
        self.roster = roster
        self.tmux = tmux
        self.cwd = cwd if cwd is not None else project_root_for_members()
        self.retry_delay = retry_delay
        self._monitor_factory = monitor_factory
        self._on_update = on_update
        self._states = {member.name: HealthState.RUNNING for member in roster.members}
        self._failures = {member.name: 0 for member in roster.members}

    def _member(self, name: str) -> Member:
        member = self.roster.get(name)
        if member is None:
            raise RosterError(f"未知成员: {name}")
        return member

    def state(self, name: str) -> HealthState:
        """读取成员当前健康状态。"""
        self._member(name)
        return self._states[name]

    def consecutive_failures(self, name: str) -> int:
        """读取最近一次成功恢复后的连续失败次数。"""
        self._member(name)
        return self._failures[name]

    def _publish(self, name: str, state: HealthState, detail: str) -> HealthUpdate:
        self._states[name] = state
        update = HealthUpdate(name, state, self._failures[name], detail)
        if self._on_update is not None:
            self._on_update(update)
        return update

    def reset_failed(self, name: str) -> HealthUpdate:
        """显式解除 failed 熔断；不会自行启动成员。"""
        self._member(name)
        self._failures[name] = 0
        return self._publish(name, HealthState.DEAD, "已重置自动拉起熔断")

    def _respawn(self, member: Member, event: CrashEvent, monitor: CrashMonitor) -> None:
        if event.kind is CrashKind.PANE_DIED and event.pane_id is not None:
            monitor.respawn(
                event.pane_id,
                window_command(member),
                cwd=str(self.cwd),
                env=member_env(member),
            )
            return
        start_member(member, self.tmux, cwd=self.cwd)

    async def handle_crash(
        self,
        name: str,
        event: CrashEvent,
        monitor: CrashMonitor,
    ) -> HealthUpdate:
        """处理一次崩溃；自动恢复失败时最多连续尝试三次。"""
        member = self._member(name)
        if self._failures[name] >= MAX_CONSECUTIVE_FAILURES:
            return self._publish(name, HealthState.FAILED, "自动拉起已熔断，停止重试")
        self._publish(name, HealthState.DEAD, f"检测到 {event.kind}，成员已失联")
        if not member.auto_respawn:
            return self._publish(name, HealthState.DEAD, "自动拉起未开启，仅告警")

        while self._failures[name] < MAX_CONSECUTIVE_FAILURES:
            try:
                self._respawn(member, event, monitor)
            except Exception as exc:
                self._failures[name] += 1
                if self._failures[name] >= MAX_CONSECUTIVE_FAILURES:
                    return self._publish(
                        name,
                        HealthState.FAILED,
                        f"连续 {MAX_CONSECUTIVE_FAILURES} 次拉起失败，停止重试: {exc}",
                    )
                self._publish(
                    name,
                    HealthState.DEAD,
                    f"第 {self._failures[name]} 次拉起失败，将重试: {exc}",
                )
                await asyncio.sleep(self.retry_delay)
                continue

            self._failures[name] = 0
            return self._publish(name, HealthState.RUNNING, "自动拉起成功")

        return self._publish(name, HealthState.FAILED, "自动拉起已熔断，停止重试")

    async def watch_once(self, name: str, *, timeout: float | None = None) -> HealthUpdate | None:
        """等待一次崩溃并处理；超时返回 ``None``。"""
        member = self._member(name)
        if not member.enabled:
            return None
        monitor = self._monitor_factory(self.tmux)
        event = await monitor.wait(member.name, timeout=timeout)
        if event is None:
            return None
        return await self.handle_crash(name, event, monitor)

    async def watch_member(self, name: str) -> HealthUpdate:
        """持续监督单个成员，直到关闭自动拉起或进入 failed。"""
        while True:
            update = await self.watch_once(name)
            if update is None:
                continue
            if update.state is not HealthState.RUNNING:
                return update

    async def watch_all(self) -> None:
        """并发监督所有启用成员；调用方取消任务即可停止。"""
        async with asyncio.TaskGroup() as group:
            for member in self.roster.enabled_members():
                group.create_task(self.watch_member(member.name), name=f"health:{member.name}")
