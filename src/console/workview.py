"""任务看板、详情、证据与关联沟通的 Textual 组件。"""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text
from textual.widgets import RichLog, Static

from bus.sanitize import sanitize
from console.theme import tokens
from console.timeline import format_timestamp
from control.tasks import task_detail_view, task_list_item_view, task_summary_view
from work import Task, TaskStatus, WorkSnapshot

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


def _status_style(status: TaskStatus) -> str:
    palette = tokens()
    if status is TaskStatus.BLOCKED:
        return palette.status["stuck"]
    if status is TaskStatus.CHANGES_REQUESTED:
        return palette.status["failed"]
    if status is TaskStatus.COMPLETED:
        return palette.status["idle"]
    if status in (TaskStatus.SUBMITTED, TaskStatus.IN_REVIEW, TaskStatus.REVIEWED):
        return palette.human
    return palette.status["working"]


def render_task_summary(snapshot: WorkSnapshot, leader: str) -> Text:
    summary = task_summary_view(snapshot, leader)
    text = Text()
    text.append("◆ 任务与证据", style=f"bold {tokens().accent}")
    text.append(f"\nLeader {sanitize(summary.leader)}", style="bold")
    text.append(
        f"\n进行中 {summary.active} · 待验收 {summary.waiting}",
        style=tokens().muted,
    )
    text.append(f"\n阻塞/退回 {summary.blocked}", style=tokens().muted)
    return text


class TaskSummaryCard(Static):
    def __init__(self, snapshot: WorkSnapshot, leader: str, **kwargs: object) -> None:
        self.snapshot = snapshot
        self.leader = leader
        super().__init__(render_task_summary(snapshot, leader), **kwargs)  # type: ignore[arg-type]

    def apply(self, snapshot: WorkSnapshot, leader: str) -> None:
        self.snapshot, self.leader = snapshot, leader
        self.update(render_task_summary(snapshot, leader))


def render_task_card(task: Task) -> Text:
    item = task_list_item_view(task)
    status = TaskStatus(item.status)
    text = Text()
    style = _status_style(status)
    text.append(f"{TASK_GLYPHS[status]} {item.id} ", style=f"bold {style}")
    text.append(item.status_label, style=style)
    text.append(f"\n{sanitize(item.title)}", style="bold")
    responsibility = f"执行 {item.assignee or '-'} · 评审 {item.reviewer or '-'}"
    text.append(f"\n{sanitize(responsibility)}", style=tokens().muted)
    return text


class TaskCard(Static):
    def __init__(self, task: Task, **kwargs: object) -> None:
        self.snapshot = task
        super().__init__(render_task_card(task), **kwargs)  # type: ignore[arg-type]


class TaskDetail(RichLog):
    """选中任务的责任、证据、事件流和关联工作对话。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(
            markup=False,
            wrap=True,
            min_width=1,
            auto_scroll=False,
            **kwargs,  # type: ignore[arg-type]
        )

    def show_empty(self, leader: str) -> None:
        self.clear()
        self.write(Text("任务看板", style=f"bold {tokens().accent}"))
        self.write(f"Leader: {sanitize(leader)}")
        self.write("尚无任务。Leader 可运行 amux task create 建立第一项任务。")

    def show_error(self, message: str) -> None:
        self.clear()
        self.write(Text(f"任务账本不可用: {sanitize(message)}", style=tokens().status["dead"]))

    def show_task(
        self,
        snapshot: WorkSnapshot,
        task: Task,
        communications: Iterable[dict[str, object]],
    ) -> None:
        detail = task_detail_view(snapshot, task, communications)
        self.clear()
        heading = Text(
            f"{detail.id}  {sanitize(detail.title)}",
            style=f"bold {tokens().accent}",
        )
        self.write(heading)
        self.write(
            f"状态:{detail.status_label}  Leader:{sanitize(detail.leader)}  "
            f"执行:{sanitize(detail.assignee or '-')}  "
            f"评审:{sanitize(detail.reviewer or '-')}"
        )
        times = (
            f"创建:{format_timestamp(detail.created_at)}  "
            f"更新:{format_timestamp(detail.updated_at)}"
        )
        if detail.completed_at:
            times += f"  完成:{format_timestamp(detail.completed_at)}"
        self.write(times)
        if detail.parent_id:
            self.write(f"父任务:{detail.parent_id}")
        if detail.children:
            child_labels = ", ".join(
                f"{child.id}({child.status_label})" for child in detail.children
            )
            self.write("子任务:" + child_labels)
        if detail.description:
            self.write(f"说明:{sanitize(detail.description)}")
        self.write(Text("证据", style="bold"))
        if detail.evidence:
            for reference in detail.evidence:
                self.write(f"  • {sanitize(reference)}")
        else:
            self.write(Text("  （尚无证据）", style=tokens().muted))
        self.write(Text("不可覆盖事件流", style="bold"))
        for event in detail.events:
            line = (
                f"  #{event.seq} {format_timestamp(event.ts)[:16]} "
                f"{event.kind_label} · "
                f"{sanitize(event.actor)}"
            )
            self.write(line)
            for event_detail in event.details:
                self.write(Text(f"     {sanitize(event_detail)}", style=tokens().muted))
        self.write(Text("关联工作对话", style="bold"))
        if not detail.communications:
            self.write(Text("  （无；用 amux msg --task 关联讨论）", style=tokens().muted))
        for entry in detail.communications:
            sender = sanitize(entry.sender)
            to = sanitize(entry.to)
            preview = sanitize(entry.preview)
            image_note = (
                f" [图片 {entry.attachment_count}]" if entry.attachment_count else ""
            )
            self.write(f"  {sender} → {to}: {preview}{image_note}")
