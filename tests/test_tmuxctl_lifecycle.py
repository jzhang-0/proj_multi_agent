"""TMX-006:pane-died/poll 崩溃检测与原地 respawn。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import CrashKind, CrashMonitor, PaneInfo, ProcessController, Tmux
from tmuxctl.errors import TmuxCommandError


def pane() -> PaneInfo:
    return PaneInfo("agent", 0, 0, "%1", 100, "sleep")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeTmux:
    def __init__(self, *, hook_available=True) -> None:
        self.panes = [pane()]
        self.dead = False
        self.dead_status = 0
        self.hook_available = hook_available
        self.hooks = {}
        self.options = {}
        self.remain = []
        self.respawns = []

    def list_panes(self, target=None, *, all_sessions=False):
        return list(self.panes)

    def display_message(self, target, format_string):
        return f"{int(self.dead)}\t{self.dead_status}"

    def set_pane_remain_on_exit(self, target, enabled=True):
        self.remain.append((target, enabled))

    def set_hook(self, name, command):
        if not self.hook_available:
            raise TmuxCommandError(["tmux", "set-hook"], 1, "unsupported")
        self.hooks[name] = command

    def unset_hook(self, name):
        self.hooks.pop(name, None)

    def show_global_option(self, name):
        return self.options.get(name)

    def unset_global_option(self, name):
        self.options.pop(name, None)

    def respawn_pane(self, target, command, *, kill=True, cwd=None, env=None):
        self.respawns.append((target, command, kill, cwd, env))


def test_hook_event_is_detected_and_cleaned_up() -> None:
    async def scenario():
        clock = Clock()
        tmux = FakeTmux()

        async def trigger(_seconds):
            clock.value += 0.01
            option = next(iter(tmux.hooks.values())).split()[2]
            tmux.options[option] = "agent\t%1\t17"

        monitor = CrashMonitor(tmux, clock=clock, sleeper=trigger)
        event = await monitor.wait("agent", timeout=1)

        assert event is not None
        assert (event.kind, event.pane_id, event.exit_status) == (CrashKind.PANE_DIED, "%1", 17)
        assert monitor.mode == "hook"
        assert tmux.remain == [("%1", True)]
        assert tmux.hooks == {} and tmux.options == {}

    asyncio.run(scenario())


def test_poll_fallback_detects_missing_session() -> None:
    async def scenario():
        clock = Clock()
        tmux = FakeTmux(hook_available=False)

        async def disappear(seconds):
            clock.value += seconds
            tmux.panes = []

        monitor = CrashMonitor(tmux, clock=clock, sleeper=disappear)
        event = await monitor.wait("agent", timeout=1)

        assert event is not None and event.kind == CrashKind.SESSION_MISSING
        assert monitor.mode == "poll"
        assert event.detected_at == 0.25

    asyncio.run(scenario())


def test_poll_detects_dead_pane_and_timeout() -> None:
    async def scenario():
        clock = Clock()
        tmux = FakeTmux(hook_available=False)
        monitor = CrashMonitor(tmux, clock=clock, sleeper=lambda seconds: advance(clock, seconds))

        assert await monitor.wait("agent", timeout=0) is None
        tmux.dead = True
        tmux.dead_status = 9
        event = await monitor.wait("agent", timeout=1)
        assert event is not None
        assert (event.kind, event.exit_status) == (CrashKind.PANE_DIED, 9)

    async def advance(clock, seconds):
        clock.value += seconds

    asyncio.run(scenario())


def test_respawn_delegates_original_pane_and_command() -> None:
    tmux = FakeTmux()
    monitor = CrashMonitor(tmux)

    monitor.respawn("%1", ["agent", "--resume"], cwd="/repo", env={"AGENT_NAME": "agent"})

    assert tmux.respawns == [
        ("%1", ["agent", "--resume"], True, "/repo", {"AGENT_NAME": "agent"})
    ]


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"tmx006-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_crash_detected_under_two_seconds_and_respawned(isolated_tmux: Tmux) -> None:
    async def scenario():
        session = f"life-{uuid.uuid4().hex[:6]}"
        isolated_tmux.new_session(session, command="sleep 30")
        monitor = CrashMonitor(isolated_tmux, poll_interval=0.05)
        controller = ProcessController(isolated_tmux)
        try:
            started = time.monotonic()
            waiter = asyncio.create_task(monitor.wait(session, timeout=2))
            await asyncio.sleep(0.1)
            assert controller.terminate(session).changed
            event = await waiter

            assert event is not None and event.kind == CrashKind.PANE_DIED
            assert time.monotonic() - started < 2
            assert event.pane_id is not None

            monitor.respawn(event.pane_id, "sleep 30")
            assert isolated_tmux.has_session(session)
            assert isolated_tmux.display_message(event.pane_id, "#{pane_dead}").strip() == "0"
        finally:
            isolated_tmux.kill_session(session, missing_ok=True)

    asyncio.run(scenario())
