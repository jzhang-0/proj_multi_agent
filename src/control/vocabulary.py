"""两端共用的状态、事件与时间线语义词表；颜色仍由各前端主题决定。"""

from __future__ import annotations

from dataclasses import dataclass

from work import STATUS_LABELS, EventKind, TaskStatus
from work.presentation import DETAIL_FIELDS, EVENT_LABELS

TASK_GLYPHS: dict[TaskStatus, str] = {
    TaskStatus.BACKLOG: "○",
    TaskStatus.ASSIGNED: "◇",
    TaskStatus.IN_PROGRESS: "▶",
    TaskStatus.BLOCKED: "!",
    TaskStatus.SUBMITTED: "↑",
    TaskStatus.IN_REVIEW: "◐",
    TaskStatus.REVIEWED: "✓",
    TaskStatus.CHANGES_REQUESTED: "↩",
    TaskStatus.ACCEPTED: "◆",
    TaskStatus.COMPLETED: "●",
}

MEMBER_STATE_GLYPHS: dict[str, tuple[str, str]] = {
    "idle": ("○", "IDLE"),
    "working": ("▶", "WORK"),
    "stuck": ("◐", "STUCK"),
    "dead": ("✕", "DEAD"),
    "failed": ("‼", "FAIL"),
}

TIMELINE_CATEGORY_LABELS = {
    "human": "human往来",
    "ai": "AI协作",
    "task": "任务",
    "control": "终端控制",
}

TIMELINE_OUTCOMES: dict[str, tuple[str, bool]] = {
    "delivered": ("✓", False),
    "shown": ("★", False),
    "deliver-failed": ("✗", True),
    "rejected": ("⊘", True),
    "malformed": ("☠", True),
    "pending": ("·", False),
}


@dataclass(frozen=True)
class VocabularyItem:
    value: str
    label: str
    glyph: str = ""


@dataclass(frozen=True)
class TimelineOutcomeItem:
    value: str
    label: str
    glyph: str
    dim: bool


@dataclass(frozen=True)
class DetailFieldItem:
    key: str
    label: str


@dataclass(frozen=True)
class Vocabulary:
    task_status: tuple[VocabularyItem, ...]
    event_kind: tuple[VocabularyItem, ...]
    member_state: tuple[VocabularyItem, ...]
    timeline_category: tuple[VocabularyItem, ...]
    timeline_outcome: tuple[TimelineOutcomeItem, ...]
    event_detail_fields: tuple[DetailFieldItem, ...]


def vocabulary() -> Vocabulary:
    outcome_labels = {
        "delivered": "已投递",
        "shown": "已显示",
        "deliver-failed": "投递失败",
        "rejected": "已拒收",
        "malformed": "格式错误",
        "pending": "待投递",
    }
    return Vocabulary(
        task_status=tuple(
            VocabularyItem(str(status), STATUS_LABELS[status], TASK_GLYPHS[status])
            for status in TaskStatus
        ),
        event_kind=tuple(
            VocabularyItem(str(kind), EVENT_LABELS[kind]) for kind in EventKind
        ),
        member_state=tuple(
            VocabularyItem(value, label, glyph)
            for value, (glyph, label) in MEMBER_STATE_GLYPHS.items()
        ),
        timeline_category=tuple(
            VocabularyItem(value, label)
            for value, label in TIMELINE_CATEGORY_LABELS.items()
        ),
        timeline_outcome=tuple(
            TimelineOutcomeItem(value, outcome_labels[value], glyph, dim)
            for value, (glyph, dim) in TIMELINE_OUTCOMES.items()
        ),
        event_detail_fields=tuple(
            DetailFieldItem(key, label) for key, label in DETAIL_FIELDS
        ),
    )
