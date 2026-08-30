"""任务摘要、列表、详情、事件与关联沟通的共享读模型。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from work import STATUS_LABELS, Task, TaskStatus, WorkSnapshot
from work.presentation import EVENT_LABELS, event_details

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


@dataclass(frozen=True)
class TaskListItemView:
    id: str
    status: str
    status_label: str
    title: str
    assignee: str | None
    reviewer: str | None


@dataclass(frozen=True)
class TaskChildView:
    id: str
    status: str
    status_label: str


@dataclass(frozen=True)
class TaskEventView:
    seq: int
    ts: str
    kind: str
    kind_label: str
    actor: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class TaskCommunicationView:
    sender: str
    to: str
    preview: str
    attachment_count: int


@dataclass(frozen=True)
class TaskDetailView:
    id: str
    title: str
    status: str
    status_label: str
    leader: str
    assignee: str | None
    reviewer: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    parent_id: str | None
    children: tuple[TaskChildView, ...]
    description: str
    evidence: tuple[str, ...]
    events: tuple[TaskEventView, ...]
    communications: tuple[TaskCommunicationView, ...]


def task_summary_view(snapshot: WorkSnapshot, leader: str) -> TaskSummaryView:
    counts = Counter(task.status for task in snapshot.tasks)
    return TaskSummaryView(
        leader=leader,
        active=sum(counts[state] for state in ACTIVE_STATES),
        waiting=sum(counts[state] for state in WAITING_STATES),
        blocked=counts[TaskStatus.BLOCKED] + counts[TaskStatus.CHANGES_REQUESTED],
    )


def task_list_item_view(task: Task) -> TaskListItemView:
    return TaskListItemView(
        id=task.id,
        status=str(task.status),
        status_label=STATUS_LABELS[task.status],
        title=task.title,
        assignee=task.assignee,
        reviewer=task.reviewer,
    )


def task_detail_view(
    snapshot: WorkSnapshot,
    task: Task,
    communications: Iterable[Mapping[str, object]],
) -> TaskDetailView:
    children = tuple(
        TaskChildView(child.id, str(child.status), STATUS_LABELS[child.status])
        for child in snapshot.children(task.id)
    )
    events = tuple(
        TaskEventView(
            seq=event.seq,
            ts=event.ts,
            kind=str(event.kind),
            kind_label=EVENT_LABELS[event.kind],
            actor=event.actor,
            details=event_details(event),
        )
        for event in snapshot.events_for(task.id)
    )
    linked: list[TaskCommunicationView] = []
    for entry in communications:
        attachments = entry.get("attachments")
        linked.append(
            TaskCommunicationView(
                sender=str(entry.get("from") or "?"),
                to=str(entry.get("to") or "?"),
                preview=str(entry.get("preview") or ""),
                attachment_count=len(attachments) if isinstance(attachments, list) else 0,
            )
        )
    return TaskDetailView(
        id=task.id,
        title=task.title,
        status=str(task.status),
        status_label=STATUS_LABELS[task.status],
        leader=task.leader,
        assignee=task.assignee,
        reviewer=task.reviewer,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.updated_at if task.completed else None,
        parent_id=task.parent_id,
        children=children,
        description=task.description,
        evidence=task.evidence,
        events=events,
        communications=tuple(linked[-20:]),
    )
