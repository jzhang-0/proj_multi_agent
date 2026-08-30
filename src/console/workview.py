"""任务看板、详情、证据与关联沟通的 Textual 组件。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from rich.text import Text
from textual.widgets import RichLog, Static

from bus.sanitize import sanitize
from console.theme import tokens
from console.timeline import format_timestamp
from work import STATUS_LABELS, Task, TaskStatus, WorkSnapshot
from work.presentation import EVENT_LABELS, event_details

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
    counts = Counter(task.status for task in snapshot.tasks)
    active_states = (
        TaskStatus.BACKLOG,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.CHANGES_REQUESTED,
    )
    waiting_states = (
        TaskStatus.SUBMITTED,
        TaskStatus.IN_REVIEW,
        TaskStatus.REVIEWED,
        TaskStatus.ACCEPTED,
    )
    active = sum(counts[state] for state in active_states)
    waiting = sum(counts[state] for state in waiting_states)
    blocked = counts[TaskStatus.BLOCKED] + counts[TaskStatus.CHANGES_REQUESTED]
    text = Text()
    text.append("◆ 任务与证据", style=f"bold {tokens().accent}")
    text.append(f"\nLeader {sanitize(leader)}", style="bold")
    text.append(f"\n进行中 {active} · 待验收 {waiting}", style=tokens().muted)
    text.append(f"\n阻塞/退回 {blocked}", style=tokens().muted)
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
    text = Text()
    style = _status_style(task.status)
    text.append(f"{TASK_GLYPHS[task.status]} {task.id} ", style=f"bold {style}")
    text.append(STATUS_LABELS[task.status], style=style)
    text.append(f"\n{sanitize(task.title)}", style="bold")
    responsibility = f"执行 {task.assignee or '-'} · 评审 {task.reviewer or '-'}"
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
        self.clear()
        heading = Text(f"{task.id}  {sanitize(task.title)}", style=f"bold {tokens().accent}")
        self.write(heading)
        self.write(
            f"状态:{STATUS_LABELS[task.status]}  Leader:{sanitize(task.leader)}  "
            f"执行:{sanitize(task.assignee or '-')}  评审:{sanitize(task.reviewer or '-')}"
        )
        times = (
            f"创建:{format_timestamp(task.created_at)}  "
            f"更新:{format_timestamp(task.updated_at)}"
        )
        if task.completed:
            times += f"  完成:{format_timestamp(task.updated_at)}"
        self.write(times)
        if task.parent_id:
            self.write(f"父任务:{task.parent_id}")
        children = snapshot.children(task.id)
        if children:
            child_labels = ", ".join(
                f"{child.id}({STATUS_LABELS[child.status]})" for child in children
            )
            self.write("子任务:" + child_labels)
        if task.description:
            self.write(f"说明:{sanitize(task.description)}")
        self.write(Text("证据", style="bold"))
        if task.evidence:
            for reference in task.evidence:
                self.write(f"  • {sanitize(reference)}")
        else:
            self.write(Text("  （尚无证据）", style=tokens().muted))
        self.write(Text("不可覆盖事件流", style="bold"))
        for event in snapshot.events_for(task.id):
            line = (
                f"  #{event.seq} {format_timestamp(event.ts)[:16]} "
                f"{EVENT_LABELS[event.kind]} · "
                f"{sanitize(event.actor)}"
            )
            self.write(line)
            for detail in event_details(event):
                self.write(Text(f"     {sanitize(detail)}", style=tokens().muted))
        self.write(Text("关联工作对话", style="bold"))
        linked = list(communications)
        if not linked:
            self.write(Text("  （无；用 amux msg --task 关联讨论）", style=tokens().muted))
        for entry in linked[-20:]:
            sender = sanitize(str(entry.get("from") or "?"))
            to = sanitize(str(entry.get("to") or "?"))
            preview = sanitize(str(entry.get("preview") or ""))
            attachments = entry.get("attachments")
            has_images = isinstance(attachments, list) and bool(attachments)
            image_note = f" [图片 {len(attachments)}]" if has_images else ""
            self.write(f"  {sender} → {to}: {preview}{image_note}")
