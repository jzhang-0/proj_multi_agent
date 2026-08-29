"""消息 schema v1 的定义与校验。

冻结契约(架构决策 §3):`to/from/text/ts` 四字段必备、含义不变;新能力
只能加可选字段(`id`/`kind`/`replyTo`/`task`),读方必须容忍未知字段。所以这里
的校验只拒绝"必备字段缺失或类型不对"和"已知可选字段类型不对",其余
未知字段原样保留在 `extra` 里带着走。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

#: 消息落盘时的时间戳格式,与 v0 `msg` 保持一致
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 必备字段(JSON 里的名字)
REQUIRED_FIELDS = ("to", "from", "text", "ts")

#: 已知可选字段(JSON 里的名字)
OPTIONAL_FIELDS = ("id", "kind", "replyTo", "task")

#: 远程身份前缀:来自 IM 群的人是 `im:小明`。它不是 `human`,所以照样受
#: 限频约束;作为收件人时也没有 tmux 会话,由网关代投(见 `bus.hub`)
REMOTE_PREFIX = "im:"


class MalformedMessage(ValueError):
    """消息结构非法。带上原因,投递循环据此写死信说明。"""


def _require_str(raw: dict[str, Any], key: str, *, allow_empty: bool) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise MalformedMessage(f"字段 {key} 必须是字符串,实际是 {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise MalformedMessage(f"字段 {key} 不能为空")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise MalformedMessage(f"可选字段 {key} 必须是字符串,实际是 {type(value).__name__}")
    return value


@dataclass(frozen=True)
class Message:
    """一条总线消息。

    `sender` 对应 JSON 里的 `from`(Python 关键字,不能直接做字段名)。
    `extra` 保存所有未知字段,序列化时原样写回。
    """

    to: str
    sender: str
    text: str
    ts: str
    id: str | None = None
    kind: str | None = None
    reply_to: str | None = None
    task: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        to: str,
        text: str,
        sender: str,
        *,
        kind: str | None = None,
        reply_to: str | None = None,
        message_id: str | None = None,
        ts: str | None = None,
        task: str | None = None,
        **extra: Any,
    ) -> Message:
        """新建一条消息,自动补 `ts` 和 `id`。"""
        return cls(
            to=to,
            sender=sender,
            text=text,
            ts=ts or time.strftime(TS_FORMAT),
            id=message_id or uuid.uuid4().hex,
            kind=kind,
            reply_to=reply_to,
            task=task,
            extra=dict(extra),
        )

    @classmethod
    def from_dict(cls, raw: Any) -> Message:
        """校验并转成 `Message`;不合法抛 `MalformedMessage`。"""
        if not isinstance(raw, dict):
            raise MalformedMessage(f"消息必须是 JSON 对象,实际是 {type(raw).__name__}")
        missing = [key for key in REQUIRED_FIELDS if key not in raw]
        if missing:
            raise MalformedMessage(f"缺少必备字段: {', '.join(missing)}")
        known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
        return cls(
            to=_require_str(raw, "to", allow_empty=False),
            sender=_require_str(raw, "from", allow_empty=False),
            text=_require_str(raw, "text", allow_empty=True),
            ts=_require_str(raw, "ts", allow_empty=False),
            id=_optional_str(raw, "id"),
            kind=_optional_str(raw, "kind"),
            reply_to=_optional_str(raw, "replyTo"),
            task=_optional_str(raw, "task"),
            extra={key: value for key, value in raw.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化成 schema v1 的 JSON 对象,四字段永远在最前。"""
        payload: dict[str, Any] = {
            "to": self.to,
            "from": self.sender,
            "text": self.text,
            "ts": self.ts,
        }
        for key, value in (
            ("id", self.id),
            ("kind", self.kind),
            ("replyTo", self.reply_to),
            ("task", self.task),
        ):
            if value is not None:
                payload[key] = value
        payload.update(self.extra)
        return payload
