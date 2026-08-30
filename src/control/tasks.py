"""任务摘要、列表、详情、事件与关联沟通的共享读模型。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from bus.sanitize import sanitize
from control.time import audit_timestamp, work_timestamp
from work import Task, TaskStatus, WorkSnapshot
from work.presentation import DETAIL_FIELDS

ACTIVE_STATES = (
    TaskStatus.BACKLOG,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
    TaskStatus.CHANGES_REQUESTED,
)
WAITING_STATES = (
    TaskStatus.SUBMITTED,
    TaskStatus.IN_REVIEW,
    TaskStatus.REVIEWED,
    TaskStatus.ACCEPTED,
)


@dataclass(frozen=True)
class TaskSummaryView:
    leader: str
    active: int
    waiting: int
    blocked: int
    total: int
    by_status: dict[str, int]


@dataclass(frozen=True)
class TaskListItemView:
    id: str
    status: str
    title: str
    assignee: str | None
    reviewer: str | None
    parent_id: str | None
    completed: bool
    created_at: float
    updated_at: float
    created_ts: str
    updated_ts: str


@dataclass(frozen=True)
class TaskChildView:
    id: str
    title: str
    status: str


@dataclass(frozen=True)
class TaskEventView:
    seq: int
    id: str
    at: float
    ts: str
    kind: str
    actor: str
    details: dict[str, str]


@dataclass(frozen=True)
class TaskCommunicationView:
    timeline_seq: int
    sender: str
    to: str
    text: str
    attachment_count: int
    at: float
    ts: str


@dataclass(frozen=True)
class TaskDetailView:
    id: str
    title: str
    status: str
    leader: str
    assignee: str | None
    reviewer: str | None
    accepted_by: str | None
    completed: bool
    latest: str
    created_at: float
    updated_at: float
    created_ts: str
    updated_ts: str
    parent_id: str | None
    children: tuple[TaskChildView, ...]
    description: str
    evidence: tuple[str, ...]
    events: tuple[TaskEventView, ...]
    communications: tuple[TaskCommunicationView, ...]


@dataclass(frozen=True)
class WorkBoardView:
    summary: TaskSummaryView
    tasks: tuple[TaskListItemView, ...]
    selected_default: str | None


def task_summary_view(snapshot: WorkSnapshot, leader: str) -> TaskSummaryView:
    counts = Counter(task.status for task in snapshot.tasks)
    by_status = {str(status): counts[status] for status in TaskStatus}
    return TaskSummaryView(
        leader=sanitize(leader),
        active=sum(counts[state] for state in ACTIVE_STATES),
        waiting=sum(counts[state] for state in WAITING_STATES),
        blocked=counts[TaskStatus.BLOCKED] + counts[TaskStatus.CHANGES_REQUESTED],
        total=len(snapshot.tasks),
        by_status=by_status,
    )


def task_list_item_view(task: Task) -> TaskListItemView:
    return TaskListItemView(
        id=task.id,
        status=str(task.status),
        title=sanitize(task.title),
        assignee=sanitize(task.assignee) if task.assignee else None,
        reviewer=sanitize(task.reviewer) if task.reviewer else None,
        parent_id=task.parent_id,
        completed=task.completed,
        created_at=work_timestamp(task.created_at),
        updated_at=work_timestamp(task.updated_at),
        created_ts=task.created_at,
        updated_ts=task.updated_at,
    )


def selected_default_task_id(snapshot: WorkSnapshot) -> str | None:
    """返回两端一致的任务默认选择。"""
    active = [task for task in snapshot.tasks if not task.completed]
    chosen = active[0] if active else (snapshot.tasks[0] if snapshot.tasks else None)
    return None if chosen is None else chosen.id


def task_board_view(snapshot: WorkSnapshot, leader: str) -> WorkBoardView:
    return WorkBoardView(
        summary=task_summary_view(snapshot, leader),
        tasks=tuple(task_list_item_view(task) for task in snapshot.tasks),
        selected_default=selected_default_task_id(snapshot),
    )


def _event_details(data: Mapping[str, object]) -> dict[str, str]:
    """只投影公开白名单，避免账本未来字段意外进入前端。"""
    details: dict[str, str] = {}
    for key, _label in DETAIL_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            details[key] = sanitize(value)
    return details


def task_communications(
    entries: Iterable[Mapping[str, object]],
    task_id: str,
) -> tuple[TaskCommunicationView, ...]:
    """从总线审计中过滤任务关联 deposit，保留最近 20 条。"""
    linked: list[TaskCommunicationView] = []
    for timeline_seq, entry in enumerate(entries, start=1):
        if entry.get("event") != "deposit" or entry.get("task") != task_id:
            continue
        attachments = entry.get("attachments")
        ts = str(entry.get("ts") or "")
        linked.append(
            TaskCommunicationView(
                timeline_seq=timeline_seq,
                sender=sanitize(str(entry.get("from") or "?")),
                to=sanitize(str(entry.get("to") or "?")),
                text=sanitize(str(entry.get("preview") or "")),
                attachment_count=(
                    len(attachments) if isinstance(attachments, list) else 0
                ),
                at=audit_timestamp(ts),
                ts=ts,
            )
        )
    return tuple(linked[-20:])


def task_detail_view(
    snapshot: WorkSnapshot,
    task: Task,
    communications: Iterable[Mapping[str, object]],
) -> TaskDetailView:
    children = tuple(
        TaskChildView(
            child.id,
            sanitize(child.title),
            str(child.status),
        )
        for child in snapshot.children(task.id)
    )
    events = tuple(
        TaskEventView(
            seq=event.seq,
            id=event.id,
            at=work_timestamp(event.ts),
            ts=event.ts,
            kind=str(event.kind),
            actor=sanitize(event.actor),
            details=_event_details(event.data),
        )
        for event in snapshot.events_for(task.id)
    )
    return TaskDetailView(
        id=task.id,
        title=sanitize(task.title),
        status=str(task.status),
        leader=sanitize(task.leader),
        assignee=sanitize(task.assignee) if task.assignee else None,
        reviewer=sanitize(task.reviewer) if task.reviewer else None,
        accepted_by=sanitize(task.accepted_by) if task.accepted_by else None,
        completed=task.completed,
        latest=sanitize(task.latest),
        created_at=work_timestamp(task.created_at),
        updated_at=work_timestamp(task.updated_at),
        created_ts=task.created_at,
        updated_ts=task.updated_at,
        parent_id=task.parent_id,
        children=children,
        description=sanitize(task.description),
        evidence=tuple(sanitize(reference) for reference in task.evidence),
        events=events,
        communications=task_communications(communications, task.id),
    )
