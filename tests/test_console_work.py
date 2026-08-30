"""TEAM-002/007：任务看板为主视图，工作对话只作关联沟通。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import ListView

from bus import Attachment, Message, deposit, pending, read_message
from bus.audit import AuditLog
from bus.paths import BusPaths
from console.app import TIMELINE_ITEM_ID, WORK_ITEM_ID, ConsoleApp
from console.compose import ComposeInput
from console.members import MemberStatusService
from console.timeline import TimelineCategory
from console.widgets import ConversationFilter, Timeline
from console.workview import TaskDetail, TaskSummaryCard
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from work import WorkService
from workspace.store import Store

MEMBERS = ("fable", "sonnet", "opus", "luna", "sol")


def _context(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    service = WorkService.for_workspace(workspace, teams=teams)
    service.create("fable", "登录页修复")
    service.assign("fable", "T-001", "sonnet")
    service.progress("sonnet", "T-001", "已定位回归")
    service.add_evidence("sonnet", "T-001", "tests/test_login.py · 4 passed")
    service.submit("sonnet", "T-001", "实现与测试已提交")
    service.request_review("fable", "T-001", "opus")
    service.review_pass("opus", "T-001", "边界和证据可信")
    service.create("fable", "更新发布说明")
    paths = BusPaths.for_workspace(workspace).ensure()
    deposit(
        Message.create(
            "sonnet",
            "请确认登录页边界",
            sender="fable",
            task="T-001",
        ),
        paths,
    )
    return workspace, service, paths


def _lines(widget: TaskDetail) -> str:
    return "\n".join("".join(segment.text for segment in line) for line in widget.lines)


def _app(workspace, service, paths):
    return ConsoleApp(
        paths,
        workspace=workspace,
        work_service=service,
        members=MEMBERS,
        member_status=MemberStatusService(MEMBERS),
        deliver=lambda _message: True,
        pump_enabled=False,
    )


def test_bound_team_opens_task_board_with_evidence_events_and_linked_chat(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)):
            sidebar = app.query_one("#members", ListView)
            assert [str(item.id) for item in sidebar.children] == [
                WORK_ITEM_ID,
                TIMELINE_ITEM_ID,
                *(f"member-{name}" for name in MEMBERS),
            ]
            assert app.active_view == "work"
            assert app.query_one("#work").display
            assert not app.query_one("#timeline").display
            assert not app.query_one("#detail").display

            summary = str(app.query_one(TaskSummaryCard).render())
            assert "Leader fable" in summary
            assert "进行中 1" in summary
            assert "待验收 1" in summary
            tasks = app.query_one("#tasks", ListView)
            assert [str(item.id) for item in tasks.children] == ["task-T-001", "task-T-002"]
            detail = _lines(app.query_one(TaskDetail))
            for expected in (
                "T-001  登录页修复",
                "Leader:fable",
                "tests/test_login.py · 4 passed",
                "不可覆盖事件流",
                "进展 · sonnet",
                "关联工作对话",
                "fable → sonnet: 请确认登录页边界",
            ):
                assert expected in detail

    asyncio.run(scenario())


def test_task_navigation_f2_f3_and_plain_input_default_to_leader(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(100, 26)) as pilot:
            tasks = app.query_one("#tasks", ListView)
            tasks.focus()
            await pilot.press("down")
            await pilot.pause()
            assert app.selected_task_id == "T-002"
            assert "T-002  更新发布说明" in _lines(app.query_one(TaskDetail))

            await pilot.press("f2")
            await pilot.pause()
            assert app.active_view == "timeline"
            await pilot.press("f3")
            await pilot.pause()
            assert app.active_view == "work"

            compose = app.query_one("#compose", ComposeInput)
            assert "Leader fable" in compose.placeholder
            assert "T-002" in compose.placeholder
            assert "@成员自动补全" in compose.placeholder
            assert "Ctrl+V图片" in compose.placeholder
            compose.focus()
            attachment = Attachment(
                path=str(workspace.state_dir / "attachments" / "layout.png"),
                media_type="image/png",
                name="layout.png",
                width=800,
                height=600,
                size=2048,
            )
            compose.attach_image(attachment)
            compose.value = "请推进并给出证据"
            await pilot.press("enter")
            await pilot.pause()

            message = read_message(pending(paths)[-1])
            assert (message.to, message.sender, message.text) == (
                "fable",
                "human",
                "请推进并给出证据",
            )
            assert message.task == "T-002"
            assert message.attachments == (attachment,)
            audit = AuditLog(paths).entries()
            assert audit[-1]["task"] == "T-002"
            assert audit[-1]["attachments"][0]["name"] == "layout.png"

    asyncio.run(scenario())


def test_task_input_at_shows_every_member_as_visible_completion(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            await pilot.press("@")
            await pilot.pause()
            assert compose.candidates == MEMBERS
            suggestions = app.query_one("#suggestions")
            assert suggestions.display
            rendered = str(suggestions.render())
            assert "自动补全 Tab/↑↓ 选择" in rendered
            assert "[@fable]" in rendered
            assert "@sol" in rendered

    asyncio.run(scenario())


def test_minimum_size_keeps_board_and_detail_readable(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(80, 24)):
            board = app.query_one("#tasks", ListView)
            detail = app.query_one(TaskDetail)
            assert board.size.width >= 25
            assert detail.size.width >= 20
            assert board.size.height >= 12
            assert detail.size.height >= 12

    asyncio.run(scenario())


def test_slash_task_opens_a_specific_detail(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(100, 26)) as pilot:
            await pilot.press("f2")
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "/task T-002"
            await pilot.press("enter")
            await pilot.pause()
            assert app.active_view == "work"
            assert app.selected_task_id == "T-002"
            assert "T-002  更新发布说明" in _lines(app.query_one(TaskDetail))

    asyncio.run(scenario())


def test_completed_task_shows_timestamps_and_task_events_are_classified(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    service.accept("fable", "T-001", "证据充分，验收通过")
    service.report("fable", "T-001", "已向 human 汇报并完成")
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            detail = _lines(app.query_one(TaskDetail))
            assert "创建:" in detail and "更新:" in detail and "完成:" in detail

            await pilot.press("f2")
            await pilot.pause()
            filters = app.query_one("#timeline-filters", ConversationFilter)
            assert filters.counts[TimelineCategory.TASK] == len(service.snapshot().events)
            timeline_text = "\n".join(
                "".join(segment.text for segment in line)
                for line in app.query_one("#timeline", Timeline).lines
            )
            assert "[任务] [T-001]" in timeline_text
            assert "派工 · 登录页修复 · 执行:sonnet" in timeline_text
            assert "任务完成 · 登录页修复" in timeline_text
            assert "完成时间:" in timeline_text

    asyncio.run(scenario())


def test_new_ledger_event_is_added_to_the_timeline_without_reloading(tmp_path: Path) -> None:
    workspace, service, paths = _context(tmp_path)
    app = _app(workspace, service, paths)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            timeline = app.query_one("#timeline", Timeline)
            before = timeline.category_counts()[TimelineCategory.TASK]
            service.assign("fable", "T-002", "sol")
            for _ in range(100):
                if timeline.category_counts()[TimelineCategory.TASK] == before + 1:
                    break
                await pilot.pause(0.02)
            assert timeline.category_counts()[TimelineCategory.TASK] == before + 1
            newest = next(
                item
                for item in reversed(timeline._history)
                if getattr(item, "category", None) is TimelineCategory.TASK
            )
            assert newest.task_id == "T-002"
            assert "派工 · 更新发布说明 · 执行:sol" in newest.text

    asyncio.run(scenario())
