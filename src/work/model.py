"""任务账本的公开模型、状态与错误。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from workspace.errors import WorkspaceError

TASK_ID_PATTERN = re.compile(r"^T-[0-9]{3,}$")


class WorkError(WorkspaceError):
    """任务账本无法完成请求。"""


class WorkValidationError(WorkError):
    """命令参数或事件内容无效。"""


class WorkPermissionError(WorkError):
    """当前 actor 没有执行这次责任流动作的权限。"""


class WorkTransitionError(WorkError):
    """任务当前状态不允许这次动作。"""


class LedgerCorruptionError(WorkError):
    """账本序号、哈希链或事件结构被破坏。"""


class EventKind(StrEnum):
    CREATED = "created"
    SPLIT = "split"
    ASSIGNED = "assigned"
    PROGRESS = "progress"
    BLOCKED = "blocked"
    EVIDENCE = "evidence"
    SUBMITTED = "submitted"
    REVIEW_REQUESTED = "review-requested"
    REVIEW_PASSED = "review-passed"
    REVIEW_RETURNED = "review-returned"
    REASSIGNED = "reassigned"
    TAKEOVER = "takeover"
    ACCEPTED = "accepted"
    REPORTED = "reported"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    IN_REVIEW = "in-review"
    REVIEWED = "reviewed"
    CHANGES_REQUESTED = "changes-requested"
    ACCEPTED = "accepted"
    COMPLETED = "completed"


STATUS_LABELS: dict[TaskStatus, str] = {
    TaskStatus.BACKLOG: "待派工",
    TaskStatus.ASSIGNED: "已派工",
    TaskStatus.IN_PROGRESS: "进行中",
    TaskStatus.BLOCKED: "阻塞",
    TaskStatus.SUBMITTED: "待评审",
    TaskStatus.IN_REVIEW: "评审中",
    TaskStatus.REVIEWED: "待验收",
    TaskStatus.CHANGES_REQUESTED: "已退回",
    TaskStatus.ACCEPTED: "待汇报",
    TaskStatus.COMPLETED: "已完成",
}


@dataclass(frozen=True)
class WorkEvent:
    """账本里的一行；``digest`` 覆盖此前哈希与本行全部业务字段。"""

    version: int
    seq: int
    id: str
    task_id: str
    kind: EventKind
    actor: str
    ts: str
    data: dict[str, Any]
    previous: str
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.version,
            "seq": self.seq,
            "id": self.id,
            "task": self.task_id,
            "type": str(self.kind),
            "actor": self.actor,
            "ts": self.ts,
            "data": self.data,
            "prev": self.previous,
            "hash": self.digest,
        }


@dataclass(frozen=True)
class Task:
    """事件重放得到的任务当前投影，不单独落盘。"""

    id: str
    title: str
    description: str
    leader: str
    parent_id: str | None
    status: TaskStatus
    created_at: str
    updated_at: str
    assignee: str | None = None
    reviewer: str | None = None
    evidence: tuple[str, ...] = ()
    latest: str = ""
    accepted_by: str | None = None
    event_ids: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status is TaskStatus.COMPLETED


@dataclass(frozen=True)
class WorkSnapshot:
    """完整账本的一次一致读取。"""

    tasks: tuple[Task, ...] = ()
    events: tuple[WorkEvent, ...] = ()
    _by_id: dict[str, Task] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {task.id: task for task in self.tasks})

    def get(self, task_id: str) -> Task:
        try:
            return self._by_id[validate_task_id(task_id)]
        except KeyError as exc:
            raise WorkValidationError(f"找不到任务 {task_id}") from exc

    def events_for(self, task_id: str) -> tuple[WorkEvent, ...]:
        validate_task_id(task_id)
        return tuple(
            event
            for event in self.events
            if event.task_id == task_id or event.data.get("parent") == task_id
        )

    def children(self, task_id: str) -> tuple[Task, ...]:
        validate_task_id(task_id)
        return tuple(task for task in self.tasks if task.parent_id == task_id)

    def next_task_id(self) -> str:
        highest = max((int(task.id[2:]) for task in self.tasks), default=0)
        return f"T-{highest + 1:03d}"


def validate_task_id(value: str) -> str:
    if not isinstance(value, str) or not TASK_ID_PATTERN.fullmatch(value):
        raise WorkValidationError("任务 ID 必须形如 T-001")
    return value


def require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkValidationError(f"{label}不能为空")
    return value.strip()
