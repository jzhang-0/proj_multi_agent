"""CON-011:tmux/server、成员会话、bus 不可写故障与自动恢复。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from bus import BusPaths
from console.app import ConsoleApp
from console.buspump import BusPump
from console.health import ConsoleHealthMonitor, Fault, FaultEvent, FaultKind
from console.members import MemberStatusService
from console.widgets import Timeline
from tmuxctl import PaneInfo, PaneSnapshot, TmuxCommandError


def pane(name: str, pane_id: str = "%1") -> PaneInfo:
    return PaneInfo(name, 0, 0, pane_id, 1234, "cat")


class FakeTmux:
    def __init__(self, sessions=()):
        self.sessions = set(sessions)
        self.server_error = False

    def list_panes(self, target=None, *, all_sessions=False):
        assert target is None and all_sessions
        if self.server_error:
            raise TmuxCommandError(["tmux", "list-panes", "-a"], 1, "no server running")
        return [pane(name, f"%{index}") for index, name in enumerate(sorted(self.sessions), 1)]


class FakeSnapshotter:
    async def capture(self, target, *, color=False, start=None):
        return PaneSnapshot(target, "screen", color, start, None, 0.0)


def timeline_text(app) -> str:
    timeline = app.query_one("#timeline", Timeline)
    return "\n".join("".join(segment.text for segment in line) for line in timeline.lines)


def test_probe_distinguishes_all_three_faults_and_suppresses_member_noise(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    tmux = FakeTmux({"claude"})
    writable = True

    def write_probe(_directory):
        if not writable:
            raise PermissionError("read-only")

    monitor = ConsoleHealthMonitor(
        paths,
        ("claude", "codex"),
        tmux,
        write_probe=write_probe,
    )
    faults = monitor.probe()
    assert [(fault.kind, fault.target) for fault in faults.values()] == [
        (FaultKind.MEMBER_SESSION, "codex")
    ]

    writable = False
    faults = monitor.probe()
    assert {fault.kind for fault in faults.values()} == {
        FaultKind.MEMBER_SESSION,
        FaultKind.BUS_UNWRITABLE,
    }

    tmux.server_error = True
    faults = monitor.probe()
    assert {fault.kind for fault in faults.values()} == {
        FaultKind.TMUX_SERVER,
        FaultKind.BUS_UNWRITABLE,
    }
    assert not any(fault.kind is FaultKind.MEMBER_SESSION for fault in faults.values())


def test_update_emits_each_alert_and_recovery_once(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    monitor = ConsoleHealthMonitor(paths, (), FakeTmux(), write_probe=lambda _path: None)
    fault = Fault(FaultKind.MEMBER_SESSION, "codex", "missing")

    assert monitor.update({fault.key: fault}) == [FaultEvent(fault)]
    assert monitor.update({fault.key: fault}) == []
    assert monitor.update({}) == [FaultEvent(fault, recovered=True)]
    assert monitor.update({}) == []


def test_monitor_reports_visible_alert_then_recovery_without_crashing(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    tmux = FakeTmux()
    tmux.server_error = True
    writable = {"value": False}

    def write_probe(_path):
        if not writable["value"]:
            raise PermissionError("read-only")

    monitor = ConsoleHealthMonitor(
        paths,
        ("codex",),
        tmux,
        interval=0.05,
        write_probe=write_probe,
    )
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=FakeSnapshotter(),
        member_status=MemberStatusService(("codex",)),
        pump_enabled=False,
        health_monitor=monitor,
    )

    async def scenario() -> None:
        async with app.run_test(size=(100, 24)) as pilot:
            for _ in range(20):
                text = timeline_text(app)
                if "tmux-server tmux" in text and "bus-unwritable" in text:
                    break
                await pilot.pause(0.02)
            text = timeline_text(app)
            assert "[告警] tmux-server tmux" in text
            assert "[告警] bus-unwritable" in text

            tmux.server_error = False
            writable["value"] = True
            for _ in range(20):
                text = timeline_text(app)
                if "[恢复] tmux-server" in text and "[恢复] bus-unwritable" in text:
                    break
                await pilot.pause(0.02)
            text = timeline_text(app)
            assert "[恢复] tmux-server tmux 已恢复" in text
            assert "[恢复] bus-unwritable" in text
            assert "[告警] member-session codex" in text

            tmux.sessions.add("codex")
            for _ in range(20):
                if "[恢复] member-session codex" in timeline_text(app):
                    break
                await pilot.pause(0.02)
            assert "[恢复] member-session codex 已恢复" in timeline_text(app)

    asyncio.run(scenario())


def test_unwritable_input_is_shown_as_alert_and_can_be_retried(tmp_path, monkeypatch) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=FakeSnapshotter(),
        member_status=MemberStatusService(("codex",)),
        pump_enabled=False,
    )
    attempts = []

    def fail_once(message, destination):
        attempts.append(message)
        if len(attempts) == 1:
            raise PermissionError("read-only")
        return destination.queue / "ok.json"

    monkeypatch.setattr("console.app.deposit", fail_once)

    async def scenario() -> None:
        async with app.run_test():
            app.send_from_input("@codex 第一次")
            assert "bus 目录不可写:PermissionError: read-only" in timeline_text(app)
            app.send_from_input("@codex 恢复后")
            assert len(attempts) == 2
            assert app.last_target == "codex"

    asyncio.run(scenario())


def test_bus_pump_can_restart_after_worker_exception(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    pump = BusPump(paths, lambda _result: None, deliver=lambda _message: True)
    calls = []

    def fail(*, stop):
        calls.append("failed")
        raise PermissionError("queue unavailable")

    pump.hub.run = fail
    pump.start()
    deadline = time.monotonic() + 1
    while pump.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert isinstance(pump.last_error, PermissionError)

    def recover(*, stop):
        calls.append("recovered")

    pump.hub.run = recover
    pump.start()
    deadline = time.monotonic() + 1
    while pump.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == ["failed", "recovered"]
    assert pump.last_error is None


def test_bus_recovery_event_restarts_pump(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=(),
        snapshotter=FakeSnapshotter(),
        member_status=MemberStatusService(()),
        pump_enabled=False,
    )

    class Pump:
        def __init__(self):
            self.starts = 0

        def start(self):
            self.starts += 1

        def stop(self):
            return None

    fake_pump = Pump()

    async def scenario() -> None:
        async with app.run_test():
            app.pump = fake_pump  # type: ignore[assignment]
            app.pump_enabled = True
            fault = Fault(FaultKind.BUS_UNWRITABLE, str(paths.queue), "read-only")
            app._on_fault_event(FaultEvent(fault, recovered=True))
            assert fake_pump.starts == 1
            assert "[恢复] bus-unwritable" in timeline_text(app)

    asyncio.run(scenario())
