"""``amux task``：成员写结构化责任事件，人和 Leader 回看任务。"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from team.store import TeamStore
from work.model import STATUS_LABELS, Task, WorkError, WorkEvent, WorkSnapshot
from work.presentation import EVENT_LABELS, event_summary
from work.service import WorkService
from workspace.errors import WorkspaceError
from workspace.resolve import ensure_from_cwd
from workspace.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux task",
        description="管理当前工作区的只追加任务账本。写操作的 actor 默认取 AGENT_NAME。",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create", help="Leader 建立任务")
    create.add_argument("title")
    create.add_argument("--description", default="")

    split = sub.add_parser("split", help="Leader 从父任务拆出子任务")
    split.add_argument("parent")
    split.add_argument("title")
    split.add_argument("--description", default="")

    assign = sub.add_parser("assign", help="Leader 首次派工")
    assign.add_argument("task")
    assign.add_argument("assignee")

    progress = sub.add_parser("progress", help="执行者记录进展")
    progress.add_argument("task")
    progress.add_argument("summary")

    block = sub.add_parser("block", help="执行者记录阻塞")
    block.add_argument("task")
    block.add_argument("reason")

    evidence = sub.add_parser("evidence", help="执行者追加可复核证据")
    evidence.add_argument("task")
    evidence.add_argument("reference")

    submit = sub.add_parser("submit", help="执行者带着已有证据提交")
    submit.add_argument("task")
    submit.add_argument("summary")

    review = sub.add_parser("review", help="Leader 指定独立评审者")
    review.add_argument("task")
    review.add_argument("reviewer")
    review.add_argument("--note", default="")

    approve = sub.add_parser("approve", help="指定评审者给出通过意见")
    approve.add_argument("task")
    approve.add_argument("note")

    returned = sub.add_parser("return", help="指定评审者退回执行者")
    returned.add_argument("task")
    returned.add_argument("reason")

    reassign = sub.add_parser("reassign", help="Leader 重新分派")
    reassign.add_argument("task")
    reassign.add_argument("assignee")
    reassign.add_argument("reason")

    takeover = sub.add_parser("takeover", help="Leader 留下完整交接记录后接管")
    takeover.add_argument("task")
    takeover.add_argument("--reason", required=True)
    takeover.add_argument("--scope", required=True)
    takeover.add_argument("--delivered", required=True)
    takeover.add_argument("--verify", required=True)

    accept = sub.add_parser("accept", help="Leader 检查证据后验收")
    accept.add_argument("task")
    accept.add_argument("conclusion")

    report = sub.add_parser("report", help="Leader 向 human 汇报并最终结项")
    report.add_argument("task")
    report.add_argument("summary")

    listing = sub.add_parser("list", help="查看任务看板")
    listing.add_argument("--status", default=None, help="按英文状态过滤")
    show = sub.add_parser("show", help="查看任务详情、证据和完整事件流")
    show.add_argument("task")
    events = sub.add_parser("events", help="查看不可覆盖事件流")
    events.add_argument("task", nargs="?")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    actor: str | None = None,
    store: Store | None = None,
    teams: TeamStore | None = None,
    cwd: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    actual_actor = actor or os.environ.get("AGENT_NAME", "human")
    try:
        workspace = ensure_from_cwd(cwd or Path.cwd(), store=store or Store.default())
        service = WorkService.for_workspace(workspace, teams=teams)
        if args.action == "list":
            return _list(service.snapshot(), output, status=args.status)
        if args.action == "show":
            return _show(service.snapshot(), args.task, output)
        if args.action == "events":
            return _events(service.snapshot(), output, task_id=args.task)
        event = _mutate(service, args, actual_actor)
        snapshot = service.snapshot()
        task = snapshot.get(event.task_id)
        print(
            f"[{event.task_id}] {EVENT_LABELS[event.kind]}已记录 · "
            f"{actual_actor} · {STATUS_LABELS[task.status]}",
            file=output,
        )
        return 0
    except (WorkError, WorkspaceError, OSError) as exc:
        print(f"[task] {exc}", file=errors)
        return 1


def _mutate(service: WorkService, args: argparse.Namespace, actor: str) -> WorkEvent:
    if args.action == "create":
        return service.create(actor, args.title, description=args.description)
    if args.action == "split":
        return service.split(actor, args.parent, args.title, description=args.description)
    if args.action == "assign":
        return service.assign(actor, args.task, args.assignee)
    if args.action == "progress":
        return service.progress(actor, args.task, args.summary)
    if args.action == "block":
        return service.block(actor, args.task, args.reason)
    if args.action == "evidence":
        return service.add_evidence(actor, args.task, args.reference)
    if args.action == "submit":
        return service.submit(actor, args.task, args.summary)
    if args.action == "review":
        return service.request_review(actor, args.task, args.reviewer, note=args.note)
    if args.action == "approve":
        return service.review_pass(actor, args.task, args.note)
    if args.action == "return":
        return service.review_return(actor, args.task, args.reason)
    if args.action == "reassign":
        return service.reassign(actor, args.task, args.assignee, args.reason)
    if args.action == "takeover":
        return service.takeover(
            actor,
            args.task,
            reason=args.reason,
            scope=args.scope,
            delivered=args.delivered,
            verification=args.verify,
        )
    if args.action == "accept":
        return service.accept(actor, args.task, args.conclusion)
    if args.action == "report":
        return service.report(actor, args.task, args.summary)
    raise AssertionError(f"未处理的 task action: {args.action}")


def _list(snapshot: WorkSnapshot, output: TextIO, *, status: str | None) -> int:
    tasks = list(snapshot.tasks)
    if status is not None:
        tasks = [task for task in tasks if task.status == status]
    if not tasks:
        print("当前筛选下没有任务。", file=output)
        return 0
    print("ID     状态       Leader    执行      评审      标题", file=output)
    for task in tasks:
        print(_task_line(task), file=output)
    return 0


def _show(snapshot: WorkSnapshot, task_id: str, output: TextIO) -> int:
    task = snapshot.get(task_id)
    print(_task_line(task), file=output)
    print(f"Leader: {task.leader}", file=output)
    if task.parent_id:
        print(f"父任务: {task.parent_id}", file=output)
    if task.description:
        print(f"说明: {task.description}", file=output)
    children = snapshot.children(task.id)
    if children:
        print(f"子任务: {', '.join(child.id for child in children)}", file=output)
    print("证据:", file=output)
    for reference in task.evidence:
        print(f"  - {reference}", file=output)
    if not task.evidence:
        print("  （无）", file=output)
    print("事件流:", file=output)
    for event in snapshot.events_for(task.id):
        print(f"  {_event_line(event)}", file=output)
    return 0


def _events(snapshot: WorkSnapshot, output: TextIO, *, task_id: str | None) -> int:
    events = snapshot.events if task_id is None else snapshot.events_for(task_id)
    for event in events:
        print(_event_line(event), file=output)
    if not events:
        print("账本尚无事件。", file=output)
    return 0


def _task_line(task: Task) -> str:
    return (
        f"{task.id:<6} {STATUS_LABELS[task.status]:<6} "
        f"{task.leader:<9} {(task.assignee or '-'):<9} {(task.reviewer or '-'):<9} {task.title}"
    )


def _event_line(event: WorkEvent) -> str:
    detail = event_summary(event)
    return (
        f"#{event.seq:<3} {event.ts[:19]} {event.task_id} "
        f"{EVENT_LABELS[event.kind]} · {event.actor}{' · ' + detail if detail else ''}"
    )
if __name__ == "__main__":
    raise SystemExit(main())
