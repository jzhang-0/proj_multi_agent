"""把总线审计和任务事件投影成与具体 UI 无关的工作对话条目。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bus import DeliveryResult
from bus.audit import AuditLog
from work import EventKind, WorkEvent, WorkSnapshot
from work.presentation import EVENT_LABELS, event_details

AUDIT_TO_OUTCOME = {
    "deposit": "pending",
    "deliver": "delivered",
    "deliver-failed": "deliver-failed",
    "rejected": "rejected",
    "malformed": "malformed",
    "control": "shown",
}

HISTORY_LIMIT = 200


class TimelineCategory(StrEnum):
    """由结构化来源字段决定的工作对话分类。"""

    HUMAN = "human"
    AI = "ai"
    TASK = "task"
    CONTROL = "control"


@dataclass(frozen=True)
class TimelineEntry:
    """一条可直接序列化的工作对话读模型。"""

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
        """兼容旧调用；UI 应先按本地时区格式化再取分钟。"""
        return self.ts[:16]

    @classmethod
    def from_result(cls, result: DeliveryResult) -> TimelineEntry:
        return from_result(result)

    @classmethod
    def from_audit(cls, entry: dict[str, Any]) -> TimelineEntry:
        return from_audit(entry)

    @classmethod
    def from_work_event(
        cls,
        event: WorkEvent,
        snapshot: WorkSnapshot,
    ) -> TimelineEntry:
        return from_work_event(event, snapshot)

    @classmethod
    def control(
        cls,
        target: str,
        text: str,
        *,
        changed: bool = True,
    ) -> TimelineEntry:
        return control_entry(target, text, changed=changed)


def from_result(result: DeliveryResult) -> TimelineEntry:
    """把一次实时投递结果投影成工作对话条目。"""
    message = result.message
    if message is None:
        return TimelineEntry(
            "",
            "bus",
            "bus",
            result.path.name,
            str(result.outcome),
            result.detail,
        )
    return TimelineEntry(
        message.ts,
        message.sender,
        message.to,
        message.text,
        str(result.outcome),
        result.detail,
        str(message.task or ""),
        len(message.attachments),
    )


def from_audit(entry: dict[str, Any]) -> TimelineEntry:
    """把一行总线/控制审计投影成工作对话条目。"""
    to = str(entry.get("to") or "?")
    audit_event = str(entry.get("event", ""))
    outcome = AUDIT_TO_OUTCOME.get(audit_event, "pending")
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
    return TimelineEntry(
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


def from_work_event(event: WorkEvent, snapshot: WorkSnapshot) -> TimelineEntry:
    """把一条责任事件投影成任务分类的工作对话条目。"""
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
        parts.append(f"完成时间:{_display_timestamp(event.ts)}")
    return TimelineEntry(
        event.ts,
        event.actor,
        target,
        " · ".join(parts),
        "shown",
        "",
        event.task_id,
        0,
        TimelineCategory.TASK,
    )


def control_entry(target: str, text: str, *, changed: bool = True) -> TimelineEntry:
    """构造一条仅用于当前 UI 即时反馈的终端控制记录。"""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return TimelineEntry(
        now,
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
    """合并审计结局和任务事件，返回最近的工作对话读模型。"""
    merged: dict[str, TimelineEntry] = {}
    order: list[str] = []
    for index, raw in enumerate(audit.entries()):
        entry = from_audit(raw)
        raw_id = raw.get("id")
        fallback = f"{entry.ts}|{entry.sender}|{entry.to}|{entry.text}"
        key = f"control:{index}" if raw.get("event") == "control" else raw_id
        if not isinstance(key, str):
            key = fallback
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
                previous.category,
            )
    entries = [merged[key] for key in order]
    if work_events and snapshot is not None:
        entries.extend(from_work_event(event, snapshot) for event in work_events)
        entries.sort(key=lambda item: _timestamp_key(item.ts))
    return entries[-limit:]


def _timestamp_key(value: str) -> float:
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def _display_timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:19]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
