"""消息 schema v1 的定义与校验。

冻结契约(架构决策 §3):`to/from/text/ts` 四字段必备、含义不变;新能力
只能加可选字段(`id`/`kind`/`replyTo`/`task`/`attachments`),读方必须容忍未知字段。所以这里
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
OPTIONAL_FIELDS = ("id", "kind", "replyTo", "task", "attachments")

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
class Attachment:
    """消息携带的本机图片引用；队列只保存元数据，不复制图片二进制。"""

    path: str
    media_type: str
    name: str
    width: int
    height: int
    size: int

    @classmethod
    def from_dict(cls, raw: Any) -> Attachment:
        if not isinstance(raw, dict):
            raise MalformedMessage("attachments 的每一项必须是对象")
        required = ("path", "mediaType", "name", "width", "height", "size")
        missing = [key for key in required if key not in raw]
        if missing:
            raise MalformedMessage(f"图片附件缺少字段: {', '.join(missing)}")
        strings = (raw["path"], raw["mediaType"], raw["name"])
        if not all(isinstance(value, str) and value.strip() for value in strings):
            raise MalformedMessage("图片附件 path/mediaType/name 必须是非空字符串")
        numbers = (raw["width"], raw["height"], raw["size"])
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in numbers
        ):
            raise MalformedMessage("图片附件 width/height/size 必须是正整数")
        if not raw["mediaType"].startswith("image/"):
            raise MalformedMessage("当前只支持图片附件")
        return cls(
            path=raw["path"],
            media_type=raw["mediaType"],
            name=raw["name"],
            width=raw["width"],
            height=raw["height"],
            size=raw["size"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mediaType": self.media_type,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "size": self.size,
        }


def _optional_attachments(raw: dict[str, Any]) -> tuple[Attachment, ...]:
    if "attachments" not in raw or raw["attachments"] is None:
        return ()
    value = raw["attachments"]
    if not isinstance(value, list):
        raise MalformedMessage("可选字段 attachments 必须是数组")
    if len(value) > 8:
        raise MalformedMessage("单条消息最多携带 8 张图片")
    return tuple(Attachment.from_dict(item) for item in value)


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
    attachments: tuple[Attachment, ...] = ()
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
        attachments: tuple[Attachment, ...] = (),
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
            attachments=tuple(attachments),
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
            attachments=_optional_attachments(raw),
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
        if self.attachments:
            payload["attachments"] = [item.to_dict() for item in self.attachments]
        payload.update(self.extra)
        return payload
