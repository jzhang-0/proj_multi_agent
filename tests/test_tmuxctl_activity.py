"""TMX-007:只按输出活动量推断 working/idle/stuck/dead。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest

from tmuxctl import ActivityMonitor, ActivityState, ActivityTracker, PaneOutputStream, Tmux


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_silent_live_member_is_idle_and_recent_output_is_working() -> None:
    clock = Clock()
    tracker = ActivityTracker(working_window=2, stuck_after=10, clock=clock)

    assert tracker.state == ActivityState.IDLE
    tracker.record_output("任意输出")
    assert tracker.state == ActivityState.WORKING

    clock.advance(2)
    assert tracker.state == ActivityState.IDLE


def test_marked_working_member_becomes_stuck_after_configured_silence() -> None:
    clock = Clock()
    tracker = ActivityTracker(working_window=1, stuck_after=5, clock=clock)
    tracker.mark_working()

    clock.advance(4.999)
    assert tracker.state == ActivityState.IDLE
    clock.advance(0.001)
    assert tracker.state == ActivityState.STUCK


def test_output_recovers_stuck_member_then_silence_makes_it_stuck_again() -> None:
    clock = Clock()
    tracker = ActivityTracker(working_window=1, stuck_after=5, clock=clock)
    tracker.mark_working()
    clock.advance(5)
    assert tracker.state == ActivityState.STUCK

    tracker.record_output("x")
    assert tracker.state == ActivityState.WORKING
    clock.advance(1)
    assert tracker.state == ActivityState.IDLE
    clock.advance(4)
    assert tracker.state == ActivityState.STUCK


def test_work_mark_after_old_output_restarts_stuck_timer() -> None:
    clock = Clock()
    tracker = ActivityTracker(working_window=1, stuck_after=5, clock=clock)
    tracker.record_output("旧输出")
    clock.advance(100)
    tracker.mark_working()

    clock.advance(4.999)
    assert tracker.state == ActivityState.IDLE
    clock.advance(0.001)
    assert tracker.state == ActivityState.STUCK


def test_dead_overrides_activity_and_revive_resets_stuck_timer() -> None:
    clock = Clock()
    tracker = ActivityTracker(working_window=1, stuck_after=2, clock=clock)
    tracker.mark_working()
    tracker.record_output(b"bytes")
    tracker.set_alive(False)
    assert tracker.state == ActivityState.DEAD

    clock.advance(10)
    tracker.set_alive(True)
    assert tracker.state == ActivityState.IDLE
    clock.advance(2)
    assert tracker.state == ActivityState.STUCK


def test_tracker_counts_bytes_but_does_not_keep_or_interpret_text() -> None:
    tracker = ActivityTracker()

    tracker.record_output("任务完成")
    tracker.record_output("ERROR")
    tracker.record_output("")
    snapshot = tracker.snapshot()

    assert snapshot.state == ActivityState.WORKING
    assert snapshot.events_in_window == 2
    assert snapshot.bytes_in_window == len("任务完成".encode()) + len(b"ERROR")
    assert not hasattr(snapshot, "text")


def test_clearing_work_mark_prevents_stuck() -> None:
    clock = Clock()
    tracker = ActivityTracker(stuck_after=1, clock=clock)
    tracker.mark_working()
    tracker.mark_working(False)
    clock.advance(100)

    assert tracker.state == ActivityState.IDLE


class FiniteStream:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk


def test_activity_monitor_consumes_stream_and_marks_dead_on_end() -> None:
    async def scenario():
        tracker = ActivityTracker()
        monitor = ActivityMonitor(tracker)

        await monitor.follow(FiniteStream(["a", "b"]))

        assert tracker.state == ActivityState.DEAD
        assert tracker.snapshot().events_in_window == 2

    asyncio.run(scenario())


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"tmx007-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_output_stream_drives_working_then_dead(isolated_tmux: Tmux) -> None:
    async def scenario():
        session = f"activity-{uuid.uuid4().hex[:6]}"
        isolated_tmux.new_session(session, command="cat")
        pane_id = isolated_tmux.list_panes(session)[0].pane_id
        tracker = ActivityTracker(working_window=1, stuck_after=5)
        monitor = ActivityMonitor(tracker)
        stream = PaneOutputStream(isolated_tmux, pane_id)
        try:
            await stream.start()
            follower = asyncio.create_task(monitor.follow(stream))
            isolated_tmux.send_keys(pane_id, "ACTIVITY", literal=True)
            isolated_tmux.send_keys(pane_id, "Enter")

            deadline = time.monotonic() + 1.0
            while tracker.state != ActivityState.WORKING and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert tracker.state == ActivityState.WORKING

            await stream.close()
            await asyncio.wait_for(follower, timeout=1)
            assert tracker.state == ActivityState.DEAD
        finally:
            await stream.close()
            isolated_tmux.kill_session(session, missing_ok=True)

    asyncio.run(scenario())
