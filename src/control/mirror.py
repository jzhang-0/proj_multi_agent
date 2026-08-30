"""WEB-011:TUI 与 Web 共用的终端画面采集核心。

单生产者/多消费者扇出、`maxsize=1` 背压、无观看者即停采集——这些与前端
无关，本该在共享层。只产出原始 ANSI 文本与几何信息；ANSI 剥离、组帧
(JSON 消息或 Rich `Text`)留在各自调用侧(`web.terminal`/`console.mirror`)，
因为本模块禁止 import rich/console/...(见 tests/test_control_plane.py
的 AST 钉子，T-015 §6.4 结论同款取舍)。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol

from tmuxctl.errors import TmuxCommandError

#: 静止画面多久发一次 idle 帧，证明连接仍活(WEB-007 定的语义)。
IDLE_PING_INTERVAL = 5.0


class MirrorCaptureTarget(Protocol):
    """采集一组一次调用拿全 `(text, cursor_y, cols, rows)`;异步接口。

    调用方各自决定怎么拿到这份数据:Web 侧包一层 `asyncio.to_thread` 转发
    给同步 tmux 客户端;TUI 侧直接转发给既有 `snapshotter.capture()`(可能
    是真 tmux,也可能是 QA 夹具的合成快照,历史上就是异步接口)。两边都在
    调用侧适配，本模块不关心底下到底是不是真 tmux。
    """

    async def capture_with_geometry(
        self, target: str, *, escape: bool = False, start: int | str | None = None
    ) -> tuple[str, int, int, int]: ...


@dataclass(frozen=True)
class RawFrame:
    """一次实际发生变化的采集；`text` 带 ANSI，未剥离。"""

    member: str
    history_offset: int
    frame_seq: int
    text: str
    cursor_y: int
    cols: int
    rows: int
    #: epoch 秒(§2.2:单调时钟不出网，出组的时间戳一律 `time.time()`)。
    captured_at: float


@dataclass(frozen=True)
class IdleTick:
    """画面没变化，证明采集仍在跑。"""

    member: str
    history_offset: int
    frame_seq: int


MirrorEvent = RawFrame | IdleTick


def _push_latest(queue: asyncio.Queue[MirrorEvent], item: MirrorEvent) -> None:
    """每订阅者一个 `maxsize=1` 队列:新帧到达先清空再放入，不阻塞采集循环。"""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(item)


@dataclass
class MirrorGroup:
    """`(member, history_offset)` 一组共享一次采集。

    `min_interval`/`idle_interval` 由第一个订阅者建组时定下，组存活期间不变
    ——TUI 与 Web 从不共处一个进程(各自独立 OS 进程)，同一组实际只会被同
    一种调用方使用，不需要动态协调多种节流预算。
    """

    member: str
    history_offset: int
    tmux: MirrorCaptureTarget
    min_interval: float
    idle_interval: float = IDLE_PING_INTERVAL
    subscribers: dict[str, asyncio.Queue[MirrorEvent]] = field(default_factory=dict)
    frame_seq: int = 0
    last_text: str | None = None
    last_broadcast_at: float = 0.0
    task: asyncio.Task[None] | None = None

    def add(self, conn_id: str) -> asyncio.Queue[MirrorEvent]:
        queue: asyncio.Queue[MirrorEvent] = asyncio.Queue(maxsize=1)
        self.subscribers[conn_id] = queue
        if self.task is None:
            self.task = asyncio.create_task(self._run())
        return queue

    def remove(self, conn_id: str) -> bool:
        """返回组内是否已空(调用方据此从 `MirrorHub` 摘除)。"""
        self.subscribers.pop(conn_id, None)
        if not self.subscribers and self.task is not None:
            self.task.cancel()
            self.task = None
        return not self.subscribers

    def _broadcast(self, item: MirrorEvent) -> None:
        for queue in self.subscribers.values():
            _push_latest(queue, item)

    async def _capture(self) -> tuple[str, int, int, int]:
        """返回 `(text, cursor_y, cols, rows)`;是否阻塞由 `tmux` 适配器自己决定。

        回滚帧的 `cursor_y` 强制归零:`#{cursor_y}` 报的是实时光标位置，
        与 `-S` 回滚起点无关，套进回滚画面里没有意义。
        """
        start = -self.history_offset if self.history_offset else None
        text, cursor_y, cols, rows = await self.tmux.capture_with_geometry(
            self.member, escape=True, start=start
        )
        if self.history_offset:
            cursor_y = 0
        return text, cursor_y, cols, rows

    async def _run(self) -> None:
        """单生产者循环，无观看者时由 `remove()` 取消。"""
        try:
            while True:
                try:
                    text, cursor_y, cols, rows = await self._capture()
                except TmuxCommandError:
                    await asyncio.sleep(self.min_interval)
                    continue
                # `now` 只用来跟 `last_broadcast_at` 比节流间隔，不出网，
                # 继续用 monotonic 没问题；出网的 captured_at 用 time.time()。
                now = time.monotonic()
                if text != self.last_text:
                    self.last_text = text
                    self.frame_seq += 1
                    self.last_broadcast_at = now
                    self._broadcast(
                        RawFrame(
                            member=self.member,
                            history_offset=self.history_offset,
                            frame_seq=self.frame_seq,
                            text=text,
                            cursor_y=cursor_y,
                            cols=cols,
                            rows=rows,
                            captured_at=time.time(),
                        )
                    )
                elif now - self.last_broadcast_at >= self.idle_interval:
                    self.last_broadcast_at = now
                    self._broadcast(
                        IdleTick(
                            member=self.member,
                            history_offset=self.history_offset,
                            frame_seq=self.frame_seq,
                        )
                    )
                await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            pass


class MirrorHub:
    """进程内单例;管理全部 `(member, history_offset)` 组。

    TUI 与 Web 各自持有一个实例(不同 OS 进程不共享内存，见 WEB-011 范围裁定)；
    组内可以有多个同类观看者(同进程多个 Web 连接，或同进程多个 TUI 详情栏)。
    """

    def __init__(self) -> None:
        self._groups: dict[tuple[str, int], MirrorGroup] = {}

    def subscribe(
        self,
        member: str,
        history_offset: int,
        conn_id: str,
        tmux: MirrorCaptureTarget,
        *,
        min_interval: float,
        idle_interval: float = IDLE_PING_INTERVAL,
    ) -> tuple[MirrorGroup, asyncio.Queue[MirrorEvent]]:
        key = (member, history_offset)
        group = self._groups.get(key)
        if group is None:
            group = MirrorGroup(
                member=member,
                history_offset=history_offset,
                tmux=tmux,
                min_interval=min_interval,
                idle_interval=idle_interval,
            )
            self._groups[key] = group
        return group, group.add(conn_id)

    def unsubscribe(self, member: str, history_offset: int, conn_id: str) -> None:
        key = (member, history_offset)
        group = self._groups.get(key)
        if group is None:
            return
        if group.remove(conn_id):
            self._groups.pop(key, None)
