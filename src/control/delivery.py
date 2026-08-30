"""Web 使用的 UI 无关 Hub 投递循环与临时静音策略。"""

from __future__ import annotations

import threading

from bus import BusPaths, Hub, Message, OutboundPolicy, Verdict
from bus.sanitize import format_for_injection
from control.lease import HubDeliveryLease
from tmuxctl import KeyInjector, TmuxError, submission_still_pending


class MutePolicy(OutboundPolicy):
    """在默认防环策略前拒绝被临时静音成员发出的消息。"""

    def __init__(self, muted: set[str]) -> None:
        super().__init__()
        self.muted = muted

    def check(self, message: Message, *, unread_backlog: int = 0) -> Verdict:
        if message.sender in self.muted:
            return Verdict.reject(f"{message.sender} 已被总控台静音(/mute),消息未投递")
        return super().check(message, unread_backlog=unread_backlog)


class DeliveryPump:
    """后台线程中的 Hub；工作区租约保证多个前端只投递一次。"""

    def __init__(
        self,
        paths: BusPaths,
        *,
        policy: OutboundPolicy,
        lease: HubDeliveryLease,
        tmux,
    ) -> None:
        self.lease = lease
        self.delivery = _BoundTmuxDelivery(tmux)
        self.hub = Hub(
            paths,
            deliver=self.delivery.deliver,
            confirm=self.delivery.confirm,
            policy=policy,
            lease_gate=lease.should_deliver,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, name="web-bus-pump", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.hub.run(stop=self._stop.is_set)
        except Exception as exc:  # pragma: no cover - surfaced for diagnostics
            self.last_error = exc

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        self.lease.release()


class _BoundTmuxDelivery:
    """Hub 的进程内提交确认，但固定复用 lifespan 已绑定的 tmux 客户端。"""

    def __init__(self, tmux) -> None:
        self.tmux = tmux
        self.pending: dict[str, str] = {}

    def deliver(self, message: Message) -> bool:
        line = format_for_injection(message)
        try:
            awaiting = self.pending.get(message.to)
            if awaiting is not None:
                return awaiting == line
            snapshot, cursor_y = self.tmux.capture_with_cursor(message.to)
            if not submission_still_pending(snapshot, cursor_y, line):
                KeyInjector(self.tmux).deliver(message.to, line)
        except TmuxError:
            return False
        self.pending[message.to] = line
        return True

    def confirm(self, target: str) -> bool:
        line = self.pending.get(target)
        if line is None:
            return True
        try:
            submitted = KeyInjector(self.tmux).ensure_submitted(target, line).submitted
        except TmuxError:
            return False
        if submitted and self.pending.get(target) == line:
            self.pending.pop(target, None)
        return submitted
