"""把总线投递循环挂在 TUI 里跑。

投递循环是阻塞的(watchfiles 监听 + 逐条投递),不能放在 Textual 的事件
循环上,否则界面会跟着卡。所以它跑在后台线程里,结果通过回调交回 UI
线程处理。停的时候只置一个标志:循环最多等一个 `poll_interval`(0.2 秒)
就会醒来退出,不 kill 线程,免得投到一半被打断。

这就是 CON-001 说的"内嵌投递循环",与 `console --headless`(纯 hub)
共用同一份 `bus.Hub`,行为不会漂。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from bus import BusPaths, DeliveryResult, Hub, Message, OutboundPolicy, Verdict
from bus.hub import tmux_deliver


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
    ) -> None:
        self.paths = paths
        self.hub = Hub(paths, deliver=deliver, on_result=on_result, policy=policy)
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
        """停循环并等线程收尾;幂等。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
