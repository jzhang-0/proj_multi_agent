"""传输层防环策略。

结论来自参考实现 `reference/pi-extensions/src/talk/policy.ts`:两个 AI 之间
的复读环必须在**传输层**终止,不能指望模型自觉。所以判定放在投递循环里
(hub 是长命进程,`./msg` 是短命进程存不住状态),被拒的消息不投递,但会
给发件人回一条说明,让它知道自己被挡了而不是消息丢了。

本模块按 Goal 逐条长大:BUS-002 去重 → BUS-003 限频 → BUS-004 熔断。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from bus.message import Message

#: 同发件人 → 同收件人的相同内容,窗口内只放行第一条
DEDUPE_WINDOW_SECONDS = 10.0

#: 总线自己发的回执用这个名字署名,回执不再产生回执
SYSTEM_SENDER = "bus"


@dataclass(frozen=True)
class Verdict:
    """一条消息的放行判定。`reason` 只在拒收时有值,会原样回执给发件人。"""

    ok: bool
    reason: str = ""

    @classmethod
    def accept(cls) -> Verdict:
        return cls(True)

    @classmethod
    def reject(cls, reason: str) -> Verdict:
        return cls(False, reason)


class OutboundPolicy:
    """按 (发件人, 收件人, 正文) 做窗口去重。

    `now` 可注入,测试用假时钟推进窗口,不睡真时间。
    """

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        dedupe_window: float = DEDUPE_WINDOW_SECONDS,
    ) -> None:
        self._now = now
        self._dedupe_window = dedupe_window
        self._recent: dict[tuple[str, str, str], float] = {}

    @staticmethod
    def _key(message: Message) -> tuple[str, str, str]:
        return (message.sender, message.to, message.text)

    def _prune(self, now: float) -> None:
        cutoff = now - self._dedupe_window
        for key, seen_at in list(self._recent.items()):
            if seen_at < cutoff:
                del self._recent[key]

    def check(self, message: Message) -> Verdict:
        """判定一条消息是否放行。不改状态,放行后由 `record` 记账。"""
        now = self._now()
        self._prune(now)
        last_seen = self._recent.get(self._key(message))
        if last_seen is not None and now - last_seen < self._dedupe_window:
            return Verdict.reject(
                f"{self._dedupe_window:.0f} 秒内你已经给 {message.to} 发过一模一样的内容,"
                "这条按防复读规则丢弃;换个说法或等对方回应"
            )
        return Verdict.accept()

    def record(self, message: Message) -> None:
        """记下一条已放行的消息,作为后续去重的基准。"""
        self._recent[self._key(message)] = self._now()


def receipt_for(message: Message, reason: str) -> Message | None:
    """给被拒的消息生成一条回执;总线自己发的消息不再回执,免得自我循环。"""
    if message.sender == SYSTEM_SENDER:
        return None
    return Message.create(
        to=message.sender,
        text=f"[总线] 你发给 {message.to} 的消息未送达:{reason}",
        sender=SYSTEM_SENDER,
        kind="receipt",
        reply_to=message.id,
    )
