"""TMX-005:pane PID/进程树与分级幂等控制。"""

from __future__ import annotations

import signal
import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import (
    ControlAction,
    PaneInfo,
    ProcessController,
    ProcessInfo,
    Tmux,
    build_process_tree,
)


def pane(pid: int = 100) -> PaneInfo:
    return PaneInfo("agent", 0, 0, "%1", pid, "sh")


def process_table() -> list[ProcessInfo]:
    return [
        ProcessInfo(100, 1, 100, "sh"),
        ProcessInfo(200, 100, 200, "python agent.py"),
        ProcessInfo(300, 200, 300, "rg pattern"),
        ProcessInfo(400, 1, 400, "unrelated"),
    ]


class FakeTmux:
    def __init__(self) -> None:
        self.panes = [pane()]
        self.keys = []
        self.sessions = {"agent"}
        self.killed_sessions = []

    def list_panes(self, target=None, *, all_sessions=False):
        return list(self.panes)

    def send_keys(self, target, *keys, literal=False):
        self.keys.append((target, keys, literal))

    def has_session(self, name):
        return name in self.sessions

    def kill_session(self, name, *, missing_ok=False):
        self.sessions.discard(name)
        self.killed_sessions.append((name, missing_ok))


def test_build_process_tree_collects_only_recursive_descendants() -> None:
    tree = build_process_tree(100, process_table())

    assert tree is not None
    assert tree.root.pid == 100
    assert [process.pid for process in tree.descendants] == [200, 300]
    assert tree.children_of(100) == (process_table()[1],)
    assert tree.cli_process.pid == 200
    assert build_process_tree(999, process_table()) is None


def test_pane_pid_and_process_tree_are_exposed() -> None:
    controller = ProcessController(FakeTmux(), process_reader=process_table)

    assert controller.pane_pid("%1") == 100
    assert controller.process_tree("%1").cli_process.pid == 200  # type: ignore[union-attr]


def test_interrupt_sends_escape_and_ctrl_c_and_missing_is_noop() -> None:
    tmux = FakeTmux()
    controller = ProcessController(tmux, process_reader=process_table)

    result = controller.interrupt("%1")
    tmux.panes = []
    repeated = controller.interrupt("%1")

    assert result.action == ControlAction.INTERRUPT and result.changed
    assert tmux.keys == [("%1", ("Escape", "C-c"), False)]
    assert not repeated.changed


@pytest.mark.parametrize(
    ("method", "expected_action", "expected_signal"),
    [
        ("terminate", ControlAction.TERMINATE, signal.SIGTERM),
        ("kill", ControlAction.KILL, signal.SIGKILL),
    ],
)
def test_signal_levels_target_cli_and_are_idempotent(method, expected_action, expected_signal):
    tmux = FakeTmux()
    signals = []

    def send_signal(pid, signum):
        signals.append((pid, signum))
        tmux.panes = []

    controller = ProcessController(tmux, process_reader=process_table, signaler=send_signal)

    first = getattr(controller, method)("%1")
    repeated = getattr(controller, method)("%1")

    assert first == first.__class__(expected_action, "%1", True, 200)
    assert signals == [(200, expected_signal)]
    assert not repeated.changed


def test_process_lookup_race_is_idempotent() -> None:
    def already_gone(pid, signum):
        raise ProcessLookupError(pid, signum)

    controller = ProcessController(
        FakeTmux(),
        process_reader=process_table,
        signaler=already_gone,
    )

    result = controller.terminate("%1")

    assert not result.changed
    assert result.pid == 200


def test_kill_session_is_idempotent() -> None:
    tmux = FakeTmux()
    controller = ProcessController(tmux, process_reader=process_table)

    first = controller.kill_session("agent")
    repeated = controller.kill_session("agent")

    assert first.changed and first.action == ControlAction.KILL_SESSION
    assert not repeated.changed
    assert tmux.killed_sessions == [("agent", True)]


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"tmx005-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_tmux_process_tree_and_idempotent_session_kill(isolated_tmux: Tmux) -> None:
    session = f"proc-{uuid.uuid4().hex[:6]}"
    isolated_tmux.new_session(session, command="sh -c 'sleep 30 & wait'")
    controller = ProcessController(isolated_tmux)
    try:
        deadline = time.monotonic() + 1.0
        tree = None
        while time.monotonic() < deadline:
            tree = controller.process_tree(session)
            if tree is not None and tree.descendants:
                break
            time.sleep(0.01)

        assert tree is not None
        assert tree.root.pid == controller.pane_pid(session)
        assert tree.descendants
        assert tree.cli_process.pid in {process.pid for process in tree.descendants}

        assert controller.kill_session(session).changed
        assert not controller.kill_session(session).changed
    finally:
        isolated_tmux.kill_session(session, missing_ok=True)
