"""窗格画面快照与高频请求合并。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

SNAPSHOT_INTERVAL_SECONDS = 0.1


class CaptureTarget(Protocol):
    """快照器需要的最小 tmux 接口。"""

    def capture_pane(
        self,
        target: str,
        *,
        escape: bool = False,
        start: int | str | None = None,
        end: int | str | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class PaneSnapshot:
    """一次窗格快照及其捕获参数。"""

    target: str
    text: str
    color: bool
    start: int | str | None
    end: int | str | None
    captured_at: float


SnapshotKey = tuple[str, bool, int | str | None, int | str | None]


class PaneSnapshotter:
    """异步获取窗格快照，并合并同一参数的高频请求。

    同一 key 的并发调用只启动一次 ``capture-pane``；完成后的
    ``min_interval`` 时间内直接返回缓存，默认把请求频率压到最多 10Hz。
    阻塞的 tmux 调用在线程中执行，不阻塞 Textual 的事件循环。
    """

    def __init__(
        self,
        tmux: CaptureTarget,
        *,
        min_interval: float = SNAPSHOT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval 不能是负数")
        self._tmux = tmux
        self._min_interval = min_interval
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cache: dict[SnapshotKey, PaneSnapshot] = {}
        self._inflight: dict[SnapshotKey, asyncio.Task[str]] = {}

    async def capture(
        self,
        target: str,
        *,
        color: bool = False,
        start: int | str | None = None,
        end: int | str | None = None,
    ) -> PaneSnapshot:
        """获取窗格画面；``color=True`` 保留 ANSI，``start`` 传给 ``-S``。"""
        key: SnapshotKey = (target, color, start, end)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and self._clock() - cached.captured_at < self._min_interval:
                return cached
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    asyncio.to_thread(
                        self._tmux.capture_pane,
                        target,
                        escape=color,
                        start=start,
                        end=end,
                    )
                )
                self._inflight[key] = task

        try:
            text = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                if self._inflight.get(key) is task:
                    del self._inflight[key]
            raise

        async with self._lock:
            snapshot = self._cache.get(key)
            if self._inflight.get(key) is task:
                snapshot = PaneSnapshot(
                    target=target,
                    text=text,
                    color=color,
                    start=start,
                    end=end,
                    captured_at=self._clock(),
                )
                self._cache[key] = snapshot
                del self._inflight[key]
            if snapshot is None:
                snapshot = PaneSnapshot(target, text, color, start, end, self._clock())
                self._cache[key] = snapshot
            return snapshot

    async def invalidate(self, target: str | None = None) -> None:
        """清掉全部缓存或指定窗格缓存；不取消正在执行的捕获。"""
        async with self._lock:
            if target is None:
                self._cache.clear()
                return
            self._cache = {key: value for key, value in self._cache.items() if key[0] != target}
