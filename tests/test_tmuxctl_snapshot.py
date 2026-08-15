"""TMX-004:带色/去色快照、历史滚动区与 10Hz 请求合并。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import PaneSnapshotter, Tmux


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeTmux:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = []
        self.delay = delay

    def capture_pane(self, target, *, escape=False, start=None, end=None):
        self.calls.append((target, escape, start, end))
        if self.delay:
            time.sleep(self.delay)
        marker = "color" if escape else "plain"
        return f"{target}:{marker}:{start}:{end}:{len(self.calls)}"


def test_snapshot_modes_and_history_are_forwarded() -> None:
    async def scenario():
        tmux = FakeTmux()
        snapshots = PaneSnapshotter(tmux, min_interval=0)

        plain = await snapshots.capture("%1")
        colored = await snapshots.capture("%1", color=True, start=-200, end=-1)

        assert not plain.color
        assert colored.color
        assert (colored.start, colored.end) == (-200, -1)
        assert tmux.calls == [
            ("%1", False, None, None),
            ("%1", True, -200, -1),
        ]

    asyncio.run(scenario())


def test_requests_inside_100ms_reuse_snapshot_then_refresh() -> None:
    async def scenario():
        clock = Clock()
        tmux = FakeTmux()
        snapshots = PaneSnapshotter(tmux, clock=clock)

        first = await snapshots.capture("%1", color=True)
        clock.value = 0.099
        reused = await snapshots.capture("%1", color=True)
        clock.value = 0.1
        refreshed = await snapshots.capture("%1", color=True)

        assert reused is first
        assert refreshed is not first
        assert len(tmux.calls) == 2

    asyncio.run(scenario())


def test_concurrent_requests_share_one_capture() -> None:
    async def scenario():
        tmux = FakeTmux(delay=0.02)
        snapshots = PaneSnapshotter(tmux)

        first, second, third = await asyncio.gather(
            snapshots.capture("%2", color=True, start="-"),
            snapshots.capture("%2", color=True, start="-"),
            snapshots.capture("%2", color=True, start="-"),
        )

        assert first is second is third
        assert tmux.calls == [("%2", True, "-", None)]

    asyncio.run(scenario())


def test_different_capture_variants_do_not_share_cache() -> None:
    async def scenario():
        tmux = FakeTmux()
        snapshots = PaneSnapshotter(tmux)

        await snapshots.capture("%3", color=False)
        await snapshots.capture("%3", color=True)
        await snapshots.capture("%3", color=True, start=-100)

        assert len(tmux.calls) == 3

    asyncio.run(scenario())


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"tmx004-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_tmux_color_plain_and_scrollback(isolated_tmux: Tmux) -> None:
    session = f"snap-{uuid.uuid4().hex[:6]}"
    command = "printf '\\033[31mRED\\033[0m\\n'; seq 1 40; sleep 30"
    isolated_tmux.new_session(session, command=command)
    try:
        deadline = time.monotonic() + 1.0
        history = ""
        while time.monotonic() < deadline:
            history = isolated_tmux.capture_pane(session, start="-")
            if "40" in history:
                break
            time.sleep(0.01)

        colored = isolated_tmux.capture_pane(session, escape=True, start="-")
        plain = isolated_tmux.capture_pane(session, start="-")

        assert "1" in history and "40" in history
        assert "RED" in colored and "\x1b[" in colored
        assert "RED" in plain and "\x1b[" not in plain
    finally:
        isolated_tmux.kill_session(session, missing_ok=True)
