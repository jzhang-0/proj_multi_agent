"""把总线投递循环挂在 TUI 里跑。

投递循环是阻塞的(watchfiles 监听 + 逐条投递),不能放在 Textual 的事件
循环上,否则界面会跟着卡。所以它跑在后台线程里,结果通过回调交回 UI
线程处理。停的时候只置一个标志:循环最多等一个 `poll_interval`(0.2 秒)
就会醒来退出,不 kill 线程,免得投到一半被打断。

这就是 CON-001 说的"内嵌投递循环",与 `console --headless`(纯 hub)
共用同一份 `bus.Hub`,行为不会漂。

WEB-002 起可以选配一把 `control.HubDeliveryLease`:多个 TUI/Web 前端挂在
同一个工作区时,每个前端的 `BusPump` 都照常起线程、照常 watch 队列,但每轮
`drain_once` 先问租约——只有持有者真正出队投递,其余前端只观察,不会重复
投递或交错键入。不传 `lease` 时行为和之前完全一样。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from bus import BusPaths, DeliveryResult, Hub, Message, OutboundPolicy, Verdict
from bus.hub import tmux_deliver
from control.lease import HubDeliveryLease


class MutePolicy(OutboundPolicy):
    """在总线默认策略之上再加一层:被 `/mute` 的成员发出的消息直接拒收。

    走策略层而不是"收到后不显示",是为了让静音和防环用同一条路:被拒的
    消息照样进审计日志、照样给发件人回执,人事后能看出它为什么没送到。
    """

    def __init__(self, muted: set[str]) -> None:
        super().__init__()
        self.muted = muted

    def check(self, message: Message, *, unread_backlog: int = 0) -> Verdict:
        if message.sender in self.muted:
            return Verdict.reject(f"{message.sender} 已被总控台静音(/mute),消息未投递")
        return super().check(message, unread_backlog=unread_backlog)


class BusPump:
    """后台线程里的投递循环。"""

    def __init__(
        self,
        paths: BusPaths,
        on_result: Callable[[DeliveryResult], None],
        deliver: Callable[[Message], bool] = tmux_deliver,
        policy: OutboundPolicy | None = None,
        lease: HubDeliveryLease | None = None,
    ) -> None:
        self.paths = paths
        self.lease = lease
        self.hub = Hub(
            paths,
            deliver=deliver,
            on_result=on_result,
            policy=policy,
            lease_gate=lease.should_deliver if lease is not None else None,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    @property
    def mode(self) -> str | None:
        """实际用的是 watch 还是 poll(起来之后才有值)。"""
        return self.hub.mode

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = None
        self._stop.clear()
        self.last_error = None
        self._thread = threading.Thread(
            target=self._run,
            name="bus-pump",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            self.hub.run(stop=self._stop.is_set)
        except Exception as exc:
            self.last_error = exc

    def stop(self, timeout: float = 2.0) -> None:
        """停循环并等线程收尾;幂等。持有的投递租约一并放弃,让下一个前端立刻能接手。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        if self.lease is not None:
            self.lease.release()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def rebind(self, paths: BusPaths, *, lease: HubDeliveryLease | None = None) -> None:
        """换一条总线:停旧循环(放弃旧租约),挂到新根上再按原开关状态拉起。

        `lease` 未传时新 pump 不接租约——旧租约是绑定旧工作区的,不能直接
        套到新根上;调用方要换工作区时应一并传入按新根构造的租约。
        """
        running = self.is_running()
        if running:
            self.stop()
        self.paths = paths
        self.lease = lease
        self.hub = Hub(
            paths,
            deliver=self.hub.deliver,
            on_result=self.hub.on_result,
            policy=self.hub.policy,
            lease_gate=lease.should_deliver if lease is not None else None,
        )
        if running:
            self.start()
