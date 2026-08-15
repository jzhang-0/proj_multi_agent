"""CON-003:时间线的着色、@ 高亮、分组、拒收灰显、历史回填、滚动回看。

渲染结果按 rich 的 `Text` 检查样式(颜色到底有没有加上),整机画面另外
按视觉自验证流程截图确认。
"""

import asyncio

import pytest
from rich.console import Console

from bus import Message, deposit
from bus.audit import AuditEvent, AuditLog
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.timeline import (
    TimelineEntry,
    highlight_mentions,
    history,
    member_color,
    render_entry,
)
from console.widgets import Timeline

#: 渲染样式需要一个 Console 上下文
RICH = Console()


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def plain_lines(app):
    log = app.query_one("#timeline", Timeline)
    return ["".join(segment.text for segment in line) for line in log.lines]


def run_async(factory):
    return asyncio.run(factory())


# --- 着色与高亮 ---------------------------------------------------------


def test_member_color_is_stable_and_distinct():
    assert member_color("claude") == member_color("claude")  # 同名同色,永远
    assert member_color("human") == "#ffd75f"
    assert member_color("bus") == "#9e9e9e"
    colors = {name: member_color(name) for name in ("claude", "codex", "cursor", "agy")}
    assert len(set(colors.values())) >= 3, f"四个成员至少三种颜色,实际 {colors}"


def test_sender_is_colored_in_its_own_color():
    entry = TimelineEntry("2026-08-16 09:00:00", "codex", "claude", "看一下", "delivered")
    line = render_entry(entry)
    segments = {segment.text: str(segment.style) for segment in line.render(RICH)}
    assert member_color("codex") in segments["codex"]
    assert "bold" in segments["codex"]


def test_mentions_are_highlighted():
    text = highlight_mentions("麻烦 @claude 看一下,别 @我 了")
    highlighted = [segment.text for segment in text.render(RICH) if "#005f87" in str(segment.style)]
    assert highlighted == ["@claude", "@我"]


def test_rejected_line_is_dim_and_carries_the_reason():
    line = render_entry(
        TimelineEntry("2026-08-16 09:00:00", "claude", "codex", "复读", "rejected", "10 秒内重复")
    )
    rendered = "".join(segment.text for segment in line.render(RICH))
    assert rendered.startswith("⊘ ")
    assert "10 秒内重复" in rendered
    assert all("dim" in str(segment.style) for segment in line.render(RICH) if segment.text.strip())


# --- 分组与回填 ---------------------------------------------------------


def test_entries_group_by_minute(paths):
    app = ConsoleApp(paths, deliver=lambda m: True, members=())

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            timeline = app.query_one("#timeline", Timeline)
            timeline.add(TimelineEntry("2026-08-16 09:00:01", "claude", "codex", "第一条"))
            timeline.add(TimelineEntry("2026-08-16 09:00:59", "codex", "claude", "同一分钟"))
            timeline.add(TimelineEntry("2026-08-16 09:01:00", "claude", "codex", "下一分钟"))
            await pilot.pause()
            headers = [line for line in plain_lines(app) if line.startswith("── 2026")]
            assert headers == ["── 2026-08-16 09:00 ────", "── 2026-08-16 09:01 ────"]

    run_async(scenario)


def test_history_merges_events_of_one_message(paths):
    audit = AuditLog(paths)
    message = Message.create("codex", "历史里的一条", sender="claude")
    audit.record(AuditEvent.DEPOSIT, message)
    audit.record(AuditEvent.DELIVER, message)
    rejected = Message.create("codex", "被挡下的一条", sender="claude")
    audit.record(AuditEvent.DEPOSIT, rejected)
    audit.record(AuditEvent.REJECTED, rejected, "10 秒内重复")

    entries = history(audit)
    assert [(e.text, e.outcome) for e in entries] == [
        ("历史里的一条", "delivered"),
        ("被挡下的一条", "rejected"),
    ]
    assert entries[1].reason == "10 秒内重复"


def test_history_merges_v0_messages_without_id(paths):
    """v0 形状的消息没有 id,也不能在时间线上重复成好几行。"""
    audit = AuditLog(paths)
    legacy = Message(to="codex", sender="claude", text="老格式", ts="2026-08-16 09:00:00")
    audit.record(AuditEvent.DEPOSIT, legacy)
    audit.record(AuditEvent.DELIVER_FAILED, legacy, "没有这个 tmux 会话")

    entries = history(audit)
    assert [(e.text, e.outcome, e.reason) for e in entries] == [
        ("老格式", "deliver-failed", "没有这个 tmux 会话")
    ]


def test_startup_backfills_history_from_the_audit_log(paths):
    audit = AuditLog(paths)
    audit.record(AuditEvent.DEPOSIT, Message.create("codex", "启动前说过的话", sender="human"))
    app = ConsoleApp(paths, deliver=lambda m: True, members=())

    async def scenario():
        async with app.run_test(size=(120, 30)):
            lines = plain_lines(app)
            assert any("启动前说过的话" in line for line in lines)
            assert any("来自 bus/log.jsonl" in line for line in lines)

    run_async(scenario)


# --- 实时追加与滚动 -----------------------------------------------------


def test_live_traffic_appends_and_scrollback_is_not_yanked(paths):
    app = ConsoleApp(paths, deliver=lambda m: True, members=())

    async def scenario():
        async with app.run_test(size=(120, 10)) as pilot:
            timeline = app.query_one("#timeline", Timeline)
            for index in range(40):
                entry = TimelineEntry("2026-08-16 09:00:00", "claude", "codex", f"第{index}条")
                timeline.add(entry)
            await pilot.pause()
            assert timeline.sticking_to_bottom

            await pilot.press("pageup")
            await pilot.pause()
            parked = timeline.scroll_offset.y
            assert not timeline.sticking_to_bottom

            deposit(Message.create("codex", "翻历史时来的新消息", sender="claude"), paths)
            for _ in range(200):
                if any("翻历史时来的新消息" in line for line in plain_lines(app)):
                    break
                await pilot.pause(0.02)
            await pilot.pause(0.05)
            assert timeline.scroll_offset.y == parked  # 没被拽回底部

            await pilot.press("end")
            await pilot.pause()
            assert timeline.sticking_to_bottom

    run_async(scenario)
