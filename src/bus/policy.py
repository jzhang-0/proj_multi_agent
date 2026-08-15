"""传输层防环策略。

去重与限频都必须由长驻的 hub 执行,不能依赖模型自觉。策略的 ``check``
只判定、不改状态;消息真正获准投递后再由 ``record`` 统一记账。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from bus.message import Message

DEDUPE_WINDOW_SECONDS = 10.0
RATE_LIMIT_MAX = 8
RATE_LIMIT_WINDOW_SECONDS = 30.0

HUMAN_SENDER = "human"
SYSTEM_SENDER = "bus"


@dataclass(frozen=True)
class Verdict:
    """一条消息的放行判定。拒收原因会由 hub 回执给发件人。"""

    ok: bool
    reason: str = ""

    @classmethod
    def accept(cls) -> Verdict:
        return cls(True)

    @classmethod
    def reject(cls, reason: str) -> Verdict:
        return cls(False, reason)


class OutboundPolicy:
    """按发送方维护滑动窗口,同时预留同内容去重状态。"""

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        dedupe_window: float = DEDUPE_WINDOW_SECONDS,
        rate_limit: int = RATE_LIMIT_MAX,
        rate_window: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self._now = now
        self._dedupe_window = dedupe_window
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self._recent: dict[tuple[str, str, str], float] = {}
        self._sent_at: dict[str, deque[float]] = {}

    @staticmethod
    def _key(message: Message) -> tuple[str, str, str]:
        return (message.sender, message.to, message.text)

    def _prune(self, now: float) -> None:
        dedupe_cutoff = now - self._dedupe_window
        for key, seen_at in list(self._recent.items()):
            if seen_at < dedupe_cutoff:
                del self._recent[key]

        rate_cutoff = now - self._rate_window
        for sender, sent_at in list(self._sent_at.items()):
            while sent_at and sent_at[0] <= rate_cutoff:
                sent_at.popleft()
            if not sent_at:
                del self._sent_at[sender]

    @staticmethod
    def _rate_limited_sender(sender: str) -> bool:
        # human 是冻结契约里的特权身份;bus 是内部回执身份,二者都不是 AI。
        return sender not in {HUMAN_SENDER, SYSTEM_SENDER}

    def check(self, message: Message) -> Verdict:
        """判定一条消息是否放行,不消耗额度。"""
        now = self._now()
        self._prune(now)

        last_seen = self._recent.get(self._key(message))
        if last_seen is not None and now - last_seen < self._dedupe_window:
            return Verdict.reject(
                f"{self._dedupe_window:.0f} 秒内你已经给 {message.to} 发过一模一样的内容,"
                "这条按防复读规则丢弃;换个说法或等对方回应"
            )

        if self._rate_limited_sender(message.sender):
            sent_at = self._sent_at.get(message.sender)
            if sent_at is not None and len(sent_at) >= self._rate_limit:
                return Verdict.reject(
                    f"发送过于频繁:{self._rate_window:.0f} 秒内每个 AI 最多发送 "
                    f"{self._rate_limit} 条;请稍后再试"
                )
        return Verdict.accept()

    def record(self, message: Message) -> None:
        """记录一条已放行消息,供后续去重与限频判定。"""
        now = self._now()
        self._recent[self._key(message)] = now
        if self._rate_limited_sender(message.sender):
            self._sent_at.setdefault(message.sender, deque()).append(now)


def receipt_for(message: Message, reason: str) -> Message | None:
    """给被拒消息生成回执;总线回执本身不再触发回执。"""
    if message.sender == SYSTEM_SENDER:
        return None
    return Message.create(
        to=message.sender,
        text=f"[总线] 你发给 {message.to} 的消息未送达:{reason}",
        sender=SYSTEM_SENDER,
        kind="receipt",
        reply_to=message.id,
    )
