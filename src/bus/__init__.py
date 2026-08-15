"""消息总线:队列、schema、投递循环。

对外入口:

- `BusPaths`:bus 根目录及其子目录,路径可注入(测试用临时目录)。
- `Message`:schema v1 的消息对象(`to/from/text/ts` 必备,`id/kind/replyTo` 可选)。
- `deposit` / `pending` / `read_message` / `archive` / `quarantine`:队列操作。
- `Hub`:投递循环,畸形消息进死信目录而不中断循环。
- `sanitize` / `format_for_injection` / `format_for_screen`:不可信文本的终端清洗
  (投递与上屏两个入口各一次)。
"""

from bus.ask import AskError, load_ask, load_reply, store_ask, store_reply, wait_for_reply
from bus.hub import DeliveryOutcome, DeliveryResult, Hub
from bus.message import MalformedMessage, Message
from bus.paths import BusPaths
from bus.policy import (
    BACKLOG_CAP,
    MAX_TEXT_BYTES,
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW_SECONDS,
    OutboundPolicy,
    Verdict,
    receipt_for,
)
from bus.queue import archive, deposit, pending, quarantine, read_message
from bus.sanitize import format_for_injection, format_for_screen, sanitize

__all__ = [
    "BACKLOG_CAP",
    "AskError",
    "BusPaths",
    "DeliveryOutcome",
    "DeliveryResult",
    "Hub",
    "MalformedMessage",
    "MAX_TEXT_BYTES",
    "Message",
    "OutboundPolicy",
    "RATE_LIMIT_MAX",
    "RATE_LIMIT_WINDOW_SECONDS",
    "Verdict",
    "archive",
    "deposit",
    "format_for_injection",
    "format_for_screen",
    "load_ask",
    "load_reply",
    "pending",
    "quarantine",
    "read_message",
    "receipt_for",
    "sanitize",
    "store_ask",
    "store_reply",
    "wait_for_reply",
]
