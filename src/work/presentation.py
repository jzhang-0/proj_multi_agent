"""CLI 与 TUI 共用的任务事件中文呈现。"""

from __future__ import annotations

from work.model import EventKind, WorkEvent

EVENT_LABELS: dict[EventKind, str] = {
    EventKind.CREATED: "建立",
    EventKind.SPLIT: "拆分",
    EventKind.ASSIGNED: "派工",
    EventKind.PROGRESS: "进展",
    EventKind.BLOCKED: "阻塞",
    EventKind.EVIDENCE: "证据",
    EventKind.SUBMITTED: "提交",
    EventKind.REVIEW_REQUESTED: "指定评审",
    EventKind.REVIEW_PASSED: "评审通过",
    EventKind.REVIEW_RETURNED: "评审退回",
    EventKind.REASSIGNED: "重新分派",
    EventKind.TAKEOVER: "Leader 接管",
    EventKind.ACCEPTED: "Leader 验收",
    EventKind.REPORTED: "向 human 汇报",
}

DETAIL_FIELDS = (
    ("summary", "摘要"),
    ("reason", "原因"),
    ("reference", "证据"),
    ("conclusion", "结论"),
    ("assignee", "执行"),
    ("reviewer", "评审"),
    ("title", "标题"),
    ("scope", "范围"),
    ("delivered", "已交付"),
    ("verification", "后续验收"),
)


def event_details(event: WorkEvent) -> tuple[str, ...]:
    details: list[str] = []
    for key, label in DETAIL_FIELDS:
        value = event.data.get(key)
        if isinstance(value, str) and value:
            details.append(f"{label}:{value}")
    return tuple(details)


def event_summary(event: WorkEvent) -> str:
    details = event_details(event)
    return details[0] if details else ""
