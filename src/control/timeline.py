"""把总线审计和任务事件投影成与具体 UI 无关的工作对话条目。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bus import DeliveryResult
from bus.audit import AuditLog
from bus.sanitize import sanitize
from control.attachments import attachment_id_from_name
from control.time import audit_timestamp, work_timestamp
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
    at: float = 0.0
    seq: int = 0
    key: str = ""
    has_body: bool = False
    kind: str = ""
    reply_to: str = ""
    attachment_ids: tuple[str, ...] = ()

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


@dataclass(frozen=True)
class TimelineSnapshotView:
    entries: tuple[TimelineEntry, ...]
    category_counts: dict[str, int]
    head_seq: int
    oldest_seq: int | None
    has_more: bool


class TimelineProjector:
    """为历史与实时投影分配同一 epoch 内稳定、递增的时间线序号。"""

    def __init__(self) -> None:
        self._next_seq = 1
        self._seq_by_key: dict[str, int] = {}

    def seed(self, entries: list[TimelineEntry]) -> None:
        self._seq_by_key = {entry.key: entry.seq for entry in entries if entry.key}
        self._next_seq = max((entry.seq for entry in entries), default=0) + 1

    def project(self, entry: TimelineEntry) -> TimelineEntry:
        seq = self._seq_by_key.get(entry.key) if entry.key else None
        if seq is None:
            seq = self._next_seq
            self._next_seq += 1
            if entry.key:
                self._seq_by_key[entry.key] = seq
        return replace(entry, seq=seq)

    def reset(self) -> None:
        self._next_seq = 1
        self._seq_by_key = {}

    def from_result(self, result: DeliveryResult) -> TimelineEntry:
        return self.project(from_result(result))

    def from_work_event(
        self,
        event: WorkEvent,
        snapshot: WorkSnapshot,
    ) -> TimelineEntry:
        return self.project(from_work_event(event, snapshot))

    def control(
        self,
        target: str,
        text: str,
        *,
        changed: bool = True,
    ) -> TimelineEntry:
        return self.project(control_entry(target, text, changed=changed))


def from_result(result: DeliveryResult) -> TimelineEntry:
    """把一次实时投递结果投影成工作对话条目。"""
    message = result.message
    if message is None:
        return TimelineEntry(
            "",
            "bus",
            "bus",
            sanitize(result.path.name),
            str(result.outcome),
            sanitize(result.detail),
            key=result.path.name,
        )
    key = message.id or f"{message.ts}|{message.sender}|{message.to}|{message.text}"
    return TimelineEntry(
        message.ts,
        sanitize(message.sender),
        sanitize(message.to),
        sanitize(message.text),
        str(result.outcome),
        sanitize(result.detail),
        sanitize(str(message.task or "")),
        len(message.attachments),
        at=audit_timestamp(message.ts),
        key=key,
        has_body=bool(message.text),
        kind=sanitize(str(message.kind or "")),
        reply_to=sanitize(str(message.reply_to or "")),
        attachment_ids=tuple(
            attachment_id
            for item in message.attachments
            if (attachment_id := attachment_id_from_name(item.name)) is not None
        ),
    )


def from_audit(entry: dict[str, Any], *, index: int | None = None) -> TimelineEntry:
    """把一行总线/控制审计投影成工作对话条目。"""
    to = sanitize(str(entry.get("to") or "?"))
    audit_event = str(entry.get("event", ""))
    outcome = AUDIT_TO_OUTCOME.get(audit_event, "pending")
    if outcome == "delivered" and to == "human":
        outcome = "shown"
    attachments = entry.get("attachments")
    attachment_count = len(attachments) if isinstance(attachments, list) else 0
    attachment_items = attachments if isinstance(attachments, list) else []
    attachment_ids = tuple(
        attachment_id
        for item in attachment_items if isinstance(item, dict)
        if isinstance(item.get("name"), str)
        if (attachment_id := attachment_id_from_name(str(item["name"]))) is not None
    )
    preview = sanitize(str(entry.get("preview", "")))
    reason = sanitize(str(entry.get("reason", "")))
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
    ts = str(entry.get("ts", ""))
    sender = sanitize(str(entry.get("from") or "bus"))
    raw_id = entry.get("id")
    fallback = f"{ts}|{sender}|{to}|{preview}"
    key = raw_id if isinstance(raw_id, str) else fallback
    if audit_event == "control" and index is not None:
        key = f"control:{index}"
    return TimelineEntry(
        ts,
        sender,
        to,
        preview,
        outcome,
        reason,
        sanitize(str(entry.get("task", ""))),
        attachment_count,
        category,
        audit_timestamp(ts),
        key=key,
        has_body=isinstance(entry.get("body"), str),
        kind=sanitize(str(entry.get("kind") or "")),
        reply_to=sanitize(str(entry.get("replyTo") or "")),
        attachment_ids=attachment_ids,
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
    parts = [action, sanitize(task.title)]
    if event.kind in (EventKind.CREATED, EventKind.SPLIT) and task.description:
        parts.append(f"详情:{sanitize(task.description)}")
    details = tuple(sanitize(detail) for detail in event_details(event))
    if event.kind in (EventKind.CREATED, EventKind.SPLIT):
        details = tuple(detail for detail in details if not detail.startswith("标题:"))
    parts.extend(details)
    if event.kind is EventKind.REPORTED:
        parts.append(f"完成时间:{_display_timestamp(event.ts)}")
    return TimelineEntry(
        event.ts,
        sanitize(event.actor),
        sanitize(target),
        " · ".join(parts),
        "shown",
        "",
        event.task_id,
        0,
        TimelineCategory.TASK,
        work_timestamp(event.ts),
        key=f"work:{event.id}",
    )


def control_entry(target: str, text: str, *, changed: bool = True) -> TimelineEntry:
    """构造一条仅用于当前 UI 即时反馈的终端控制记录。"""
    current = datetime.now(UTC)
    now = current.isoformat().replace("+00:00", "Z")
    return TimelineEntry(
        now,
        "human",
        sanitize(target),
        sanitize(text),
        "shown" if changed else "deliver-failed",
        category=TimelineCategory.CONTROL,
        at=current.timestamp(),
        key=f"control-live:{current.timestamp()}",
    )


def history(
    audit: AuditLog,
    limit: int = HISTORY_LIMIT,
    *,
    work_events: tuple[WorkEvent, ...] = (),
    snapshot: WorkSnapshot | None = None,
) -> list[TimelineEntry]:
    """合并审计结局和任务事件，返回最近的工作对话读模型。"""
    return history_from_entries(
        audit.entries(),
        limit,
        work_events=work_events,
        snapshot=snapshot,
    )


def by_display_time(entries: Iterable[TimelineEntry]) -> list[TimelineEntry]:
    """展示顺序：按 ``at``，seq 只作同秒稳定次序。seq 本身不是时序。"""
    return sorted(entries, key=lambda item: (item.at, item.seq, item.key))


def history_from_entries(
    raw_entries: Iterable[dict[str, Any]],
    limit: int = HISTORY_LIMIT,
    *,
    work_events: tuple[WorkEvent, ...] = (),
    snapshot: WorkSnapshot | None = None,
    projector: TimelineProjector | None = None,
) -> list[TimelineEntry]:
    """从完整审计序列投影时间线，按到达顺序分配全量稳定序号。

    到达顺序：审计 jsonl 首次出现的 key（文件序）+ 账本事件（账本序）。
    不按 ``at`` 排序后再 enumerate——那会让后到、更早 ``at`` 的记录挤占
    已分配 seq。传入同一 ``projector`` 时，已知 key 的 seq 在后续重建中
    保持不变。返回列表按 seq 升序；展示层再 ``by_display_time``。
    """
    merged: dict[str, TimelineEntry] = {}
    order: list[str] = []
    for index, raw in enumerate(raw_entries):
        entry = from_audit(raw, index=index)
        key = entry.key
        previous = merged.get(key)
        if previous is None:
            merged[key] = entry
            order.append(key)
            continue
        if entry.outcome != "pending":
            merged[key] = replace(
                previous,
                outcome=entry.outcome,
                reason=entry.reason,
                task_id=entry.task_id,
                attachment_count=entry.attachment_count or previous.attachment_count,
                has_body=entry.has_body or previous.has_body,
                kind=entry.kind or previous.kind,
                reply_to=entry.reply_to or previous.reply_to,
                attachment_ids=entry.attachment_ids or previous.attachment_ids,
            )
    arrived = [merged[key] for key in order]
    if work_events and snapshot is not None:
        arrived.extend(from_work_event(event, snapshot) for event in work_events)
    owner = projector or TimelineProjector()
    sequenced = [owner.project(item) for item in arrived]
    sequenced.sort(key=lambda item: item.seq)
    return sequenced[-limit:] if limit > 0 else []


def timeline_snapshot_view(
    audit: AuditLog,
    limit: int = HISTORY_LIMIT,
    *,
    work_events: tuple[WorkEvent, ...] = (),
    snapshot: WorkSnapshot | None = None,
) -> TimelineSnapshotView:
    """返回分页条目和由完整投影计算的分类计数。"""
    if limit <= 0:
        raise ValueError("limit 必须大于 0")
    raw_entries = audit.entries()
    all_entries = history_from_entries(
        raw_entries,
        max(1, len(raw_entries) + len(work_events)),
        work_events=work_events,
        snapshot=snapshot,
    )
    counts = {"all": len(all_entries), **{str(category): 0 for category in TimelineCategory}}
    for entry in all_entries:
        counts[str(entry.resolved_category)] += 1
    entries = tuple(all_entries[-limit:])
    return TimelineSnapshotView(
        entries=entries,
        category_counts=counts,
        head_seq=max((entry.seq for entry in all_entries), default=0),
        oldest_seq=min((entry.seq for entry in entries), default=None),
        has_more=len(all_entries) > len(entries),
    )


def _display_timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:19]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
