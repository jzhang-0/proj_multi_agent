"""成员状态汇总及可序列化的成员卡读模型。"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bus import BusPaths
from bus.queue import pending, read_message
from roster.load import load_effective_roster
from roster.schema import RosterError
from tmuxctl import ActivityMonitor, ActivityTracker, PaneOutputStream, Tmux

MEMBER_STATES = frozenset({"idle", "working", "stuck", "dead", "failed"})


@dataclass(frozen=True)
class MemberCardSnapshot:
    """成员卡快照；绝不暴露 ActivityTracker 的进程内单调时钟值。"""

    name: str
    state: str
    queued: int
    silent_for: float | None
    alive: bool
    source: str
    snapshot_at: float


def pending_counts(paths: BusPaths) -> Counter[str]:
    """按收件人统计尚未投递的合法队列消息。"""
    counts: Counter[str] = Counter()
    for queued_path in pending(paths):
        try:
            counts[read_message(queued_path).to] += 1
        except (OSError, ValueError):
            continue
    return counts


StreamFactory = Callable[[Tmux, str], PaneOutputStream]


class MemberStatusService:
    """汇总 pane 活性、存活覆盖和待投递数量，供任意前端读取。"""

    def __init__(
        self,
        names: tuple[str, ...],
        tmux: Tmux | None = None,
        *,
        working_window: float = 2.0,
        stuck_after: float = 120.0,
        reconnect_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        stream_factory: StreamFactory = PaneOutputStream,
        sources: Mapping[str, str] | None = None,
    ) -> None:
        self.names = names
        self.tmux = tmux
        self.clock = clock
        self.wall_clock = wall_clock
        self.reconnect_interval = reconnect_interval
        self._stream_factory = stream_factory
        self._working_window = working_window
        self._stuck_after = stuck_after
        self._trackers = {name: self._new_tracker() for name in names}
        self._sources = {name: "roster" for name in names}
        if sources is not None:
            self._sources.update(sources)
        self._overrides: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False

    def _new_tracker(self) -> ActivityTracker:
        return ActivityTracker(
            working_window=self._working_window,
            stuck_after=self._stuck_after,
            clock=self.clock,
        )

    def track(
        self,
        names: tuple[str, ...],
        *,
        sources: Mapping[str, str] | None = None,
    ) -> None:
        self.names = names
        for name in names:
            if name not in self._trackers:
                self._trackers[name] = self._new_tracker()
            self._sources.setdefault(name, "roster")
        if sources is not None:
            self._sources.update(sources)

    @property
    def can_monitor(self) -> bool:
        return self.tmux is not None

    def _tracker(self, name: str) -> ActivityTracker:
        try:
            return self._trackers[name]
        except KeyError as exc:
            raise ValueError(f"未知成员: {name}") from exc

    def record_output(self, name: str, output: str | bytes) -> None:
        self._tracker(name).record_output(output)

    def mark_working(self, name: str, marked: bool = True) -> None:
        self._tracker(name).mark_working(marked)

    def set_alive(self, name: str, alive: bool) -> None:
        self._tracker(name).set_alive(alive)

    def override_state(self, name: str, state: str | None) -> None:
        self._tracker(name)
        if state is None:
            self._overrides.pop(name, None)
            return
        if state not in MEMBER_STATES:
            raise ValueError(f"未知成员状态: {state}")
        self._overrides[name] = state

    def mark_failed(self, name: str, failed: bool = True) -> None:
        self.override_state(name, "failed" if failed else None)

    def snapshot(self, name: str, *, queued: int = 0) -> MemberCardSnapshot:
        activity = self._tracker(name).snapshot()
        return MemberCardSnapshot(
            name=name,
            state=self._overrides.get(name, str(activity.state)),
            queued=queued,
            silent_for=(
                None if activity.last_output_at is None else activity.silent_for
            ),
            alive=activity.alive,
            source=self._sources.get(name, "roster"),
            snapshot_at=self.wall_clock(),
        )

    async def _watch_member(self, name: str) -> None:
        assert self.tmux is not None
        tracker = self._tracker(name)
        while not self._stopped:
            stream: PaneOutputStream | None = None
            try:
                panes = self.tmux.list_panes(name)
                if len(panes) != 1:
                    tracker.set_alive(False)
                else:
                    tracker.set_alive(True)
                    stream = self._stream_factory(self.tmux, panes[0].pane_id)
                    await ActivityMonitor(tracker).follow(stream)
            except Exception:
                tracker.set_alive(False)
            finally:
                if stream is not None:
                    await stream.close()
            if not self._stopped:
                await asyncio.sleep(self.reconnect_interval)

    async def run(self) -> None:
        if self.tmux is None:
            return
        self._stopped = False
        try:
            async with asyncio.TaskGroup() as group:
                for name in self.names:
                    task = group.create_task(
                        self._watch_member(name), name=f"member-status:{name}"
                    )
                    self._tasks.add(task)
        finally:
            self._tasks.clear()

    def stop(self) -> None:
        self._stopped = True
        for task in tuple(self._tasks):
            task.cancel()


def member_names(*, cwd: str | Path | None = None) -> tuple[str, ...]:
    """返回当前项目有效名册中的成员名。"""
    try:
        roster = load_effective_roster(cwd=cwd)
    except (RosterError, OSError):
        return ()
    return tuple(member.name for member in roster.enabled_members())
