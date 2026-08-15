"""消息总线:队列、schema、投递循环。

对外入口:

- `BusPaths`:bus 根目录及其子目录,路径可注入(测试用临时目录)。
- `Message`:schema v1 的消息对象(`to/from/text/ts` 必备,`id/kind/replyTo` 可选)。
- `deposit` / `pending` / `read_message` / `archive` / `quarantine`:队列操作。
- `Hub`:投递循环,畸形消息进死信目录而不中断循环。
"""

from bus.hub import DeliveryOutcome, DeliveryResult, Hub
from bus.message import MalformedMessage, Message
from bus.paths import BusPaths
from bus.policy import (
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW_SECONDS,
    OutboundPolicy,
    Verdict,
    receipt_for,
)
from bus.queue import archive, deposit, pending, quarantine, read_message

__all__ = [
    "BusPaths",
    "DeliveryOutcome",
    "DeliveryResult",
    "Hub",
    "MalformedMessage",
    "Message",
    "OutboundPolicy",
    "RATE_LIMIT_MAX",
    "RATE_LIMIT_WINDOW_SECONDS",
    "Verdict",
    "archive",
    "deposit",
    "pending",
    "quarantine",
    "read_message",
    "receipt_for",
]
