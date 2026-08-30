"""工作对话记录:总线流量怎么变成一行行画面。

三个约定:

- **发件人固定着色**:颜色由名字算出来(crc32 取模),同一个成员在任何
  一次运行、任何一台机器上都是同一个颜色,不随名册顺序变。
- **拒收是灰的**:被防环策略挡下的消息不是正常发言,整行压暗并带原因,
  一眼能和真实发言区分开。
- **按分钟分组**:同一分钟内的消息只在组头写一次时间,行内不再重复,
  省出来的宽度给正文。

历史回填读 `bus/log.jsonl`(BUS-008 的审计日志):一条消息可能有多条事件
(deposit → deliver/rejected),按 `id` 合并成一行,取最后的结局。
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from rich.text import Text

from bus import DeliveryResult
from bus.audit import AuditLog
from bus.sanitize import sanitize
from console.theme import tokens
from work import EventKind, WorkEvent, WorkSnapshot
from work.presentation import EVENT_LABELS, event_details

#: 结局 → (标记, 是否压暗)。拒收/失败都不是正常发言
OUTCOME_MARKS = {
    "delivered": ("✓", False),
    "shown": ("★", False),
    "deliver-failed": ("✗", True),
    "rejected": ("⊘", True),
    "malformed": ("☠", True),
    "pending": ("·", False),
}

#: 审计事件名 → 时间线结局。`deposit` 只说明消息进了队列,还不是结局
AUDIT_TO_OUTCOME = {
    "deposit": "pending",
    "deliver": "delivered",
    "deliver-failed": "deliver-failed",
    "rejected": "rejected",
    "malformed": "malformed",
    "control": "shown",
}

#: `@名字`:字母数字下划线连字符,或中文
MENTION = re.compile(r"@[\w一-鿿-]+")

#: 回填多少条历史
HISTORY_LIMIT = 200


class TimelineCategory(StrEnum):
    """工作对话记录的结构化分类；来源于事件字段，不解析正文。"""

    HUMAN = "human"
    AI = "ai"
    TASK = "task"
    CONTROL = "control"


CATEGORY_LABELS: dict[TimelineCategory, str] = {
    TimelineCategory.HUMAN: "human往来",
    TimelineCategory.AI: "AI协作",
    TimelineCategory.TASK: "任务",
    TimelineCategory.CONTROL: "终端控制",
}


def format_timestamp(value: str) -> str:
    """把账本 UTC ISO 时间转成本机时间；旧消息时间保持原样。"""
    if not value:
        return ""
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:19]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def member_color(name: str) -> str:
    """按名字算固定颜色:同一个名字在同一套主题里永远同一个色。

    颜色本身来自当前主题的 token(`console.theme`),换主题只换色号,
    "谁是几号色"这件事不变。
    """
    palette = tokens()
    if name == "human":
        return palette.human
    if name == "bus":
        return palette.bus
    return palette.members[zlib.crc32(name.encode("utf-8")) % len(palette.members)]


@dataclass(frozen=True)
class TimelineEntry:
    """时间线上的一行:一条消息 + 它的结局。"""

    ts: str
    sender: str
    to: str
    text: str
    outcome: str = "pending"
    reason: str = ""
    task_id: str = ""
    attachment_count: int = 0
    category: TimelineCategory | None = None

    @property
    def resolved_category(self) -> TimelineCategory:
        if self.category is not None:
            return self.category
        if (
            self.sender == "human"
            or self.to == "human"
            or self.sender.startswith("im:")
            or self.to.startswith("im:")
        ):
            return TimelineCategory.HUMAN
        return TimelineCategory.AI

    @property
    def group(self) -> str:
        """分组键:精确到分钟。"""
        return self.ts[:16]

    @classmethod
    def from_result(cls, result: DeliveryResult) -> TimelineEntry:
        message = result.message
        if message is None:
            return cls("", "bus", "bus", result.path.name, str(result.outcome), result.detail)
        return cls(
            message.ts,
            message.sender,
            message.to,
            message.text,
            str(result.outcome),
            result.detail,
            str(message.task or ""),
            len(message.attachments),
        )

    @classmethod
    def from_audit(cls, entry: dict[str, Any]) -> TimelineEntry:
        to = str(entry.get("to") or "?")
        audit_event = str(entry.get("event", ""))
        outcome = AUDIT_TO_OUTCOME.get(audit_event, "pending")
        # 审计日志把"送到人的屏幕上"也记成 deliver,时间线上要还原成 ★,
        # 免得历史和实时两种画法对同一条消息给出不同标记
        if outcome == "delivered" and to == "human":
            outcome = "shown"
        attachments = entry.get("attachments")
        attachment_count = len(attachments) if isinstance(attachments, list) else 0
        preview = str(entry.get("preview", ""))
        reason = str(entry.get("reason", ""))
        category = None
        if audit_event == "control":
            category = TimelineCategory.CONTROL
            action = str(entry.get("action") or preview)
            label = {
                "key": "按键",
                "type": "直接输入",
                "interrupt": "打断",
                "terminate": "终止",
                "restart": "重启",
                "takeover": "完整接管",
            }.get(action, action)
            preview = f"{label}{' · ' + reason if reason else ''}"
            reason = ""
            if entry.get("changed") is False:
                outcome = "deliver-failed"
        return cls(
            str(entry.get("ts", "")),
            str(entry.get("from") or "bus"),
            to,
            preview,
            outcome,
            reason,
            str(entry.get("task", "")),
            attachment_count,
            category,
        )

    @classmethod
    def from_work_event(
        cls,
        event: WorkEvent,
        snapshot: WorkSnapshot,
    ) -> TimelineEntry:
        """把责任账本事件投影成一条带任务详情的记录。"""
        task = snapshot.get(event.task_id)
        if event.kind in (EventKind.CREATED, EventKind.SPLIT):
            target = "任务账本"
        elif event.kind is EventKind.REPORTED:
            target = "human"
        elif event.data.get("assignee") or event.data.get("reviewer"):
            target = str(event.data.get("assignee") or event.data.get("reviewer"))
        elif event.actor != task.leader:
            target = task.leader
        else:
            target = task.assignee or "任务账本"
        action = "任务完成" if event.kind is EventKind.REPORTED else EVENT_LABELS[event.kind]
        parts = [action, task.title]
        if event.kind in (EventKind.CREATED, EventKind.SPLIT) and task.description:
            parts.append(f"详情:{task.description}")
        details = event_details(event)
        if event.kind in (EventKind.CREATED, EventKind.SPLIT):
            details = tuple(detail for detail in details if not detail.startswith("标题:"))
        parts.extend(details)
        if event.kind is EventKind.REPORTED:
            parts.append(f"完成时间:{format_timestamp(event.ts)}")
        return cls(
            format_timestamp(event.ts),
            event.actor,
            target,
            " · ".join(parts),
            "shown",
            "",
            event.task_id,
            0,
            TimelineCategory.TASK,
        )

    @classmethod
    def control(
        cls,
        target: str,
        text: str,
        *,
        changed: bool = True,
    ) -> TimelineEntry:
        return cls(
            time.strftime("%Y-%m-%d %H:%M:%S"),
            "human",
            target,
            text,
            "shown" if changed else "deliver-failed",
            category=TimelineCategory.CONTROL,
        )


def history(
    audit: AuditLog,
    limit: int = HISTORY_LIMIT,
    *,
    work_events: tuple[WorkEvent, ...] = (),
    snapshot: WorkSnapshot | None = None,
) -> list[TimelineEntry]:
    """从审计日志回填启动前的历史。

    同一条消息的多个事件合并成一行,结局取最后一个非 `deposit` 的事件。
    没有 `id` 的老消息(v0 形状的四字段 JSON)按 时间+收发+预览 当键,
    否则一条消息会在时间线上重复出现好几行。
    """
    merged: dict[str, TimelineEntry] = {}
    order: list[str] = []
    for index, raw in enumerate(audit.entries()):
        entry = TimelineEntry.from_audit(raw)
        raw_id = raw.get("id")
        fallback = f"{entry.ts}|{entry.sender}|{entry.to}|{entry.text}"
        if raw.get("event") == "control":
            key = f"control:{index}"
        else:
            key = raw_id if isinstance(raw_id, str) else fallback
        previous = merged.get(key)
        if previous is None:
            merged[key] = entry
            order.append(key)
            continue
        if entry.outcome != "pending":
            merged[key] = TimelineEntry(
                previous.ts,
                previous.sender,
                previous.to,
                previous.text,
                entry.outcome,
                entry.reason,
                entry.task_id,
                entry.attachment_count or previous.attachment_count,
            )
    entries = [merged[key] for key in order]
    if work_events and snapshot is not None:
        entries.extend(TimelineEntry.from_work_event(event, snapshot) for event in work_events)
        entries.sort(key=lambda item: item.ts)
    return entries[-limit:]


def group_header(group: str) -> Text:
    """时间戳分组的组头。"""
    return Text(f"── {group} " + "─" * 4, style=tokens().divider)


def divider(label: str) -> Text:
    return Text(f"── {label} " + "─" * 4, style=tokens().divider)


def render_entry(entry: TimelineEntry) -> Text:
    """把一行渲染成带样式的文本。上屏文本一律先清洗(BUS-005)。"""
    palette = tokens()
    mark, dimmed = OUTCOME_MARKS.get(entry.outcome, ("?", True))
    line = Text(no_wrap=False)
    line.append(f"{mark} ", style=palette.muted if dimmed else palette.divider)
    category = entry.resolved_category
    category_style = {
        TimelineCategory.HUMAN: palette.human,
        TimelineCategory.AI: palette.status["working"],
        TimelineCategory.TASK: palette.status["idle"],
        TimelineCategory.CONTROL: palette.muted,
    }[category]
    line.append(f"[{CATEGORY_LABELS[category]}] ", style=f"bold {category_style}")
    if entry.task_id:
        line.append(f"[{sanitize(entry.task_id)}] ", style=f"bold {palette.accent}")
    line.append(sanitize(entry.sender), style=f"bold {member_color(entry.sender)}")
    line.append(" → ", style=palette.divider)
    line.append(sanitize(entry.to), style=member_color(entry.to))
    line.append(": ", style=palette.divider)
    line.append_text(highlight_mentions(sanitize(entry.text)))
    if entry.attachment_count:
        line.append(f" [图片 {entry.attachment_count}]", style=f"bold {palette.accent}")
    if entry.reason:
        line.append(f"  ({sanitize(entry.reason)})", style=palette.muted)
    if dimmed:
        line.stylize("dim")
    return line


def highlight_mentions(text: str) -> Text:
    """`@名字` 加亮,其余按普通正文。"""
    rendered = Text()
    cursor = 0
    for match in MENTION.finditer(text):
        rendered.append(text[cursor : match.start()])
        palette = tokens()
        rendered.append(
            match.group(0), style=f"bold {palette.accent_text} on {palette.accent}"
        )
        cursor = match.end()
    rendered.append(text[cursor:])
    return rendered
