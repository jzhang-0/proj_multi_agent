"""CON-005:五态成员卡、队列数、相对活动时间与 1 秒内刷新。"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
import uuid
from collections.abc import Iterator

import pytest
from rich.console import Console

from bus import BusPaths, DeliveryOutcome, DeliveryResult, Message, deposit
from console.app import ConsoleApp
from console.members import (
    STATUS_PRESENTATION,
    MemberCardSnapshot,
    MemberStatusService,
    pending_counts,
    relative_activity,
)
from console.widgets import MemberCard, render_member_card
from tmuxctl import Tmux

RICH = Console()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def plain(rendered) -> str:
    return "".join(segment.text for segment in rendered.render(RICH))


def test_five_states_have_distinct_shape_label_and_color() -> None:
    assert set(STATUS_PRESENTATION) == {"idle", "working", "stuck", "dead", "failed"}
    glyphs = {value[0] for value in STATUS_PRESENTATION.values()}
    labels = {value[1] for value in STATUS_PRESENTATION.values()}
    colors = {value[2] for value in STATUS_PRESENTATION.values()}
    assert len(glyphs) == len(labels) == len(colors) == 5

    for state, (glyph, label, color) in STATUS_PRESENTATION.items():
        rendered = render_member_card(
            MemberCardSnapshot("codex", state, 3, 5.0, True, "roster")
        )
        assert plain(rendered).splitlines() == [f"{glyph} {label:<5} codex", "排队3 · 5秒前"]
        badge_styles = [
            str(segment.style) for segment in rendered.render(RICH) if glyph in segment.text
        ]
        assert badge_styles and color in badge_styles[0] and "bold" in badge_styles[0]


@pytest.mark.parametrize(
    ("last_at", "now", "expected"),
    [
        (None, 100, "未活动"),
        (100, 100.9, "刚刚"),
        (100, 105, "5秒前"),
        (100, 220, "2分前"),
        (100, 7300, "2时前"),
        (100, 172900, "2天前"),
    ],
)
def test_relative_activity(last_at, now, expected) -> None:
    assert relative_activity(last_at, now) == expected


def test_pending_counts_are_per_recipient_and_ignore_malformed(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    deposit(Message.create("codex", "one", "human"), paths, audit=False)
    deposit(Message.create("codex", "two", "human"), paths, audit=False)
    deposit(Message.create("claude", "one", "human"), paths, audit=False)
    (paths.queue / "broken.json").write_text("not json", encoding="utf-8")

    counts = pending_counts(paths)
    assert counts == {"codex": 2, "claude": 1}


def test_status_service_is_driven_by_activity_tracker_and_failed_override() -> None:
    clock = Clock()
    service = MemberStatusService(
        ("codex",),
        working_window=1,
        stuck_after=5,
        clock=clock,
    )
    assert service.snapshot("codex").state == "idle"

    service.record_output("codex", "任意输出")
    assert service.snapshot("codex").state == "working"
    assert service.snapshot("codex").silent_for == 0

    clock.advance(1)
    assert service.snapshot("codex").state == "idle"
    service.mark_working("codex")
    clock.advance(5)
    assert service.snapshot("codex").state == "stuck"

    service.set_alive("codex", False)
    assert service.snapshot("codex").state == "dead"
    service.mark_failed("codex")
    assert service.snapshot("codex").state == "failed"
    service.mark_failed("codex", False)
    assert service.snapshot("codex").state == "dead"


def test_member_card_refreshes_state_and_queue_within_one_second(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    service = MemberStatusService(("codex",))
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        member_status=service,
        pump_enabled=False,
    )

    async def scenario() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            card = app.query_one("#card-codex", MemberCard)
            assert card.snapshot.state == "idle"
            deposit(Message.create("codex", "排队", "human"), paths, audit=False)
            started = time.monotonic()
            service.record_output("codex", "activity")
            while card.snapshot.state != "working" and time.monotonic() - started < 1:
                await pilot.pause(0.05)
            assert time.monotonic() - started < 1
            assert card.snapshot.queued == 1
            assert "排队1" in plain(render_member_card(card.snapshot))

            service.mark_failed("codex")
            started = time.monotonic()
            while card.snapshot.state != "failed" and time.monotonic() - started < 1:
                await pilot.pause(0.05)
            assert time.monotonic() - started < 1

    asyncio.run(scenario())


def test_successful_delivery_marks_recipient_for_stuck_detection(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    clock = Clock()
    service = MemberStatusService(("codex",), stuck_after=5, clock=clock)
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        member_status=service,
        pump_enabled=False,
    )

    async def scenario() -> None:
        async with app.run_test():
            message = Message.create("codex", "开始任务", "human")
            app.show_result(
                DeliveryResult(paths.queue / "done.json", DeliveryOutcome.DELIVERED, message)
            )
            clock.advance(5)
            assert service.snapshot("codex").state == "stuck"

    asyncio.run(scenario())


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"con5-{uuid.uuid4().hex[:8]}")
    try:
        yield client
    finally:
        client.kill_server()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")
def test_real_pane_output_reaches_member_status_under_one_second(isolated_tmux: Tmux) -> None:
    async def scenario() -> None:
        name = f"member-{uuid.uuid4().hex[:6]}"
        isolated_tmux.new_session(name, command="cat")
        service = MemberStatusService(
            (name,), isolated_tmux, working_window=1, reconnect_interval=0.05
        )
        task = asyncio.create_task(service.run())
        try:
            await asyncio.sleep(0.15)
            started = time.monotonic()
            isolated_tmux.send_keys(name, "OUTPUT", literal=True)
            isolated_tmux.send_keys(name, "Enter")
            while service.snapshot(name).state != "working" and time.monotonic() - started < 1:
                await asyncio.sleep(0.01)
            assert service.snapshot(name).state == "working"
            assert time.monotonic() - started < 1
        finally:
            service.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())
