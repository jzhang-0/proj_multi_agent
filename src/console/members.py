"""成员栏的数据来源:名册。

名册读不出来(文件缺失、格式错)不该把界面拖垮——总控台照样要能起来
看总线流量,所以这里出错就回一个空列表,由界面提示。
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from bus import BusPaths
from bus.queue import pending, read_message
from roster.load import load_roster
from roster.schema import RosterError
from tmuxctl import ActivityMonitor, ActivityTracker, PaneOutputStream, Tmux

STATUS_PRESENTATION = {
    "idle": ("○", "IDLE", "#87afff"),
    "working": ("▶", "WORK", "#5fd7ff"),
    "stuck": ("◐", "STUCK", "#ffd75f"),
    "dead": ("✕", "DEAD", "#ff5f5f"),
    "failed": ("‼", "FAIL", "#ff00af"),
}


@dataclass(frozen=True)
class MemberCardSnapshot:
    """成员卡片一次刷新所需的全部数据。"""

    name: str
    state: str
    queued: int
    last_activity: str


def relative_activity(last_at: float | None, now: float) -> str:
    """把单调时钟时间戳格式化为紧凑的相对时间。"""
    if last_at is None:
        return "未活动"
    seconds = max(0, int(now - last_at))
    if seconds < 1:
        return "刚刚"
    if seconds < 60:
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}时前"
    return f"{hours // 24}天前"


def pending_counts(paths: BusPaths) -> Counter[str]:
    """按收件人统计尚未投递的合法队列消息。"""
    counts: Counter[str] = Counter()
    for path in pending(paths):
        try:
            counts[read_message(path).to] += 1
        except (OSError, ValueError):
            continue
    return counts


StreamFactory = Callable[[Tmux, str], PaneOutputStream]


class MemberStatusService:
    """把 TMX-007 活性追踪器接到每个成员的 pane 输出流。"""

    def __init__(
        self,
        names: tuple[str, ...],
        tmux: Tmux | None = None,
        *,
        working_window: float = 2.0,
        stuck_after: float = 120.0,
        reconnect_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        stream_factory: StreamFactory = PaneOutputStream,
    ) -> None:
        self.names = names
        self.tmux = tmux
        self.clock = clock
        self.reconnect_interval = reconnect_interval
        self._stream_factory = stream_factory
        self._trackers = {
            name: ActivityTracker(
                working_window=working_window,
                stuck_after=stuck_after,
                clock=clock,
            )
            for name in names
        }
        self._overrides: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False

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
        """接入 ROS-004 failed 状态，也供确定性的视觉取证使用。"""
        self._tracker(name)
        if state is None:
            self._overrides.pop(name, None)
            return
        if state not in STATUS_PRESENTATION:
            raise ValueError(f"未知成员状态: {state}")
        self._overrides[name] = state

    def mark_failed(self, name: str, failed: bool = True) -> None:
        self.override_state(name, "failed" if failed else None)

    def snapshot(self, name: str, *, queued: int = 0) -> MemberCardSnapshot:
        activity = self._tracker(name).snapshot()
        state = self._overrides.get(name, str(activity.state))
        return MemberCardSnapshot(
            name=name,
            state=state,
            queued=queued,
            last_activity=relative_activity(activity.last_output_at, self.clock()),
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
        """持续订阅全部成员；由 Textual worker 的生命周期负责取消。"""
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


def member_names() -> tuple[str, ...]:
    """名册里启用的成员名,顺序与 `roster.toml` 一致。"""
    try:
        roster = load_roster()
    except (RosterError, OSError):
        return ()
    return tuple(member.name for member in roster.enabled_members())
