"""TMX-002: 按键注入与半行字隔离。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import KeyInjector, Tmux, last_line_uncommitted


class FakeTmux:
    def __init__(self, snapshots: list[str] | str) -> None:
        self._snapshots = [snapshots] if isinstance(snapshots, str) else list(snapshots)
        self.calls: list[tuple[str, tuple[str, ...], bool]] = []

    def capture_pane(self, target: str, **_kwargs: object) -> str:
        if not self._snapshots:
            return "$ "
        if len(self._snapshots) == 1:
            return self._snapshots[0]
        return self._snapshots.pop(0)

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None:
        self.calls.append((target, keys, literal))


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.mark.parametrize(
    ("snapshot", "busy"),
    [
        ("", False),
        ("$ ", False),
        ("bash-3.2$ ", False),
        ("user@host:~/proj$ ", False),
        (">>> ", False),
        ("> ", False),
        ("└────────────┘", False),
        ("$ echo hel\n\n\n\n", True),
        ("│ > hello wor    │\n└────────────┘", True),
        ("│ >              │\n└────────────┘", False),
        ("$ echo hel", True),
        ("> hello wor", True),
        ("partial line without prompt", True),
    ],
)
def test_last_line_heuristic(snapshot: str, busy: bool) -> None:
    assert last_line_uncommitted(snapshot) is busy


def test_idle_injects_literal_then_enter() -> None:
    tmux = FakeTmux("$ ")
    outcome = KeyInjector(tmux).text("claude", "hello")
    assert outcome.waited is False
    assert outcome.isolated is False
    assert tmux.calls == [
        ("claude", ("hello",), True),
        ("claude", ("Enter",), False),
    ]


def test_busy_then_idle_waits_without_isolate() -> None:
    clock = FakeClock()
    tmux = FakeTmux(["$ echo hel", "$ "])
    injector = KeyInjector(
        tmux, max_wait_s=0.5, poll_s=0.1, clock=clock.now, sleeper=clock.sleep
    )
    outcome = injector.text("claude", "msg")
    assert outcome.waited is True
    assert outcome.isolated is False
    assert tmux.calls[0] == ("claude", ("msg",), True)
    assert tmux.calls[1] == ("claude", ("Enter",), False)


def test_still_busy_newline_isolates() -> None:
    clock = FakeClock()
    tmux = FakeTmux("$ echo hel")
    injector = KeyInjector(
        tmux, max_wait_s=0.2, poll_s=0.1, clock=clock.now, sleeper=clock.sleep
    )
    outcome = injector.text("claude", "msg")
    assert outcome.waited is True
    assert outcome.isolated is True
    assert tmux.calls[0] == ("claude", ("Enter",), False)
    assert tmux.calls[1] == ("claude", ("msg",), True)
    assert tmux.calls[2] == ("claude", ("Enter",), False)


def test_control_keys_skip_busy_check() -> None:
    tmux = FakeTmux("$ echo hel")
    injector = KeyInjector(tmux)
    injector.enter("s")
    injector.escape("s")
    injector.interrupt("s")
    assert tmux.calls == [
        ("s", ("Enter",), False),
        ("s", ("Escape",), False),
        ("s", ("C-c",), False),
    ]


def test_text_without_submit() -> None:
    tmux = FakeTmux("$ ")
    KeyInjector(tmux).text("s", "hello", submit=False)
    assert tmux.calls == [("s", ("hello",), True)]


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    socket = f"tmx002-{uuid.uuid4().hex[:8]}"
    client = Tmux(socket_name=socket, timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_tmux_isolates_half_line(isolated_tmux: Tmux) -> None:
    name = f"sess-{uuid.uuid4().hex[:6]}"
    isolated_tmux.new_session(
        name,
        command=["bash", "--norc", "--noprofile"],
        env={"PS1": "$ ", "TERM": "dumb"},
    )
    try:
        for _ in range(20):
            snapshot = isolated_tmux.capture_pane(name)
            if snapshot.strip() and not last_line_uncommitted(snapshot):
                break
            time.sleep(0.05)
        isolated_tmux.send_keys(name, "echo HELLO", literal=True)
        assert last_line_uncommitted(isolated_tmux.capture_pane(name)) is True
        clock = FakeClock()
        injector = KeyInjector(
            isolated_tmux,
            max_wait_s=0.2,
            poll_s=0.05,
            clock=clock.now,
            sleeper=clock.sleep,
        )
        outcome = injector.text(name, "echo WORLD")
        assert outcome.isolated is True
        time.sleep(0.15)
        pane = isolated_tmux.capture_pane(name)
        assert "HELLO" in pane
        assert "WORLD" in pane
        assert "HELLOecho" not in pane
    finally:
        isolated_tmux.kill_session(name, missing_ok=True)
