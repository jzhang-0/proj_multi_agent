"""只按输出活动量推断成员活性，不解析终端语义。"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

DEFAULT_WORKING_WINDOW_SECONDS = 2.0
DEFAULT_STUCK_AFTER_SECONDS = 120.0


class ActivityState(StrEnum):
    WORKING = "working"
    IDLE = "idle"
    STUCK = "stuck"
    DEAD = "dead"


@dataclass(frozen=True)
class ActivitySample:
    """一次非空输出活动；不保存正文。"""

    at: float
    bytes: int


@dataclass(frozen=True)
class ActivitySnapshot:
    """某一时刻可直接供 roster/console 使用的活性快照。"""

    state: ActivityState
    alive: bool
    marked_working: bool
    last_output_at: float | None
    silent_for: float
    events_in_window: int
    bytes_in_window: int


class OutputStream(Protocol):
    def __aiter__(self) -> AsyncIterator[str]: ...


class ActivityTracker:
    """基于非空输出事件和外部工作标记推断四态。"""

    def __init__(
        self,
        *,
        working_window: float = DEFAULT_WORKING_WINDOW_SECONDS,
        stuck_after: float = DEFAULT_STUCK_AFTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if working_window <= 0:
            raise ValueError("working_window 必须大于 0")
        if stuck_after <= 0:
            raise ValueError("stuck_after 必须大于 0")
        self.working_window = working_window
        self.stuck_after = stuck_after
        self._clock = clock
        self._created_at = clock()
        self._samples: deque[ActivitySample] = deque()
        self._last_output_at: float | None = None
        self._marked_working_at: float | None = None
        self._alive = True

    def record_output(self, output: str | bytes) -> None:
        """记录非空输出的字节量；正文立即丢弃。"""
        size = len(output) if isinstance(output, bytes) else len(output.encode("utf-8"))
        if size == 0:
            return
        now = self._clock()
        self._samples.append(ActivitySample(now, size))
        self._last_output_at = now
        self._alive = True
        self._prune(now)

    def mark_working(self, marked: bool = True) -> None:
        """设置外部“正在执行任务”标记,仅用于 stuck 判定。"""
        if marked:
            if self._marked_working_at is None:
                self._marked_working_at = self._clock()
            return
        self._marked_working_at = None

    def set_alive(self, alive: bool) -> None:
        """更新进程存活状态；恢复时重新开始 stuck 静默计时。"""
        if alive and not self._alive and self._marked_working_at is not None:
            self._marked_working_at = self._clock()
        self._alive = alive

    def _prune(self, now: float) -> None:
        cutoff = now - self.working_window
        while self._samples and self._samples[0].at <= cutoff:
            self._samples.popleft()

    def snapshot(self) -> ActivitySnapshot:
        """按当前时钟计算状态。"""
        now = self._clock()
        self._prune(now)
        references = [self._created_at]
        if self._last_output_at is not None:
            references.append(self._last_output_at)
        if self._marked_working_at is not None:
            references.append(self._marked_working_at)
        reference = max(references)
        silent_for = max(0.0, now - reference)

        if not self._alive:
            state = ActivityState.DEAD
        elif self._samples:
            state = ActivityState.WORKING
        elif self._marked_working_at is not None and silent_for >= self.stuck_after:
            state = ActivityState.STUCK
        else:
            state = ActivityState.IDLE

        return ActivitySnapshot(
            state=state,
            alive=self._alive,
            marked_working=self._marked_working_at is not None,
            last_output_at=self._last_output_at,
            silent_for=silent_for,
            events_in_window=len(self._samples),
            bytes_in_window=sum(sample.bytes for sample in self._samples),
        )

    @property
    def state(self) -> ActivityState:
        return self.snapshot().state


class ActivityMonitor:
    """把 ``PaneOutputStream`` 等异步输出源持续喂给 tracker。"""

    def __init__(self, tracker: ActivityTracker | None = None) -> None:
        self.tracker = tracker or ActivityTracker()

    async def follow(self, stream: OutputStream, *, mark_dead_on_end: bool = True) -> None:
        """消费输出直到流结束；默认把结束/异常视为 dead。"""
        self.tracker.set_alive(True)
        try:
            async for output in stream:
                self.tracker.record_output(output)
        finally:
            if mark_dead_on_end:
                self.tracker.set_alive(False)
