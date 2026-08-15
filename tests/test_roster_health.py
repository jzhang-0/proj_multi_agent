"""ROS-004:成员健康告警、可配置自动拉起与三次失败熔断。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from roster.health import HealthState, HealthSupervisor
from roster.schema import Member, Roster, roster_from_dict
from tmuxctl import CrashEvent, CrashKind, Tmux


class FakeTmux:
    def __init__(self, sessions=()):
        self.sessions = set(sessions)
        self.new_calls = []

    def has_session(self, name):
        return name in self.sessions

    def new_session(self, name, **kwargs):
        self.sessions.add(name)
        self.new_calls.append((name, kwargs))


class FakeMonitor:
    def __init__(self, event=None, failures=0):
        self.event = event
        self.failures = failures
        self.wait_calls = []
        self.respawn_calls = []

    async def wait(self, target, *, timeout=None):
        self.wait_calls.append((target, timeout))
        return self.event

    def respawn(self, target, command, *, cwd=None, env=None):
        self.respawn_calls.append((target, command, cwd, env))
        if len(self.respawn_calls) <= self.failures:
            raise RuntimeError(f"boom-{len(self.respawn_calls)}")


def member(*, auto_respawn=False):
    return Member(
        name="codex",
        command="agent",
        args=("--resume",),
        env={"LANG": "C"},
        greeting_template="hi {name}",
        auto_respawn=auto_respawn,
    )


def pane_dead() -> CrashEvent:
    return CrashEvent(CrashKind.PANE_DIED, "codex", "%1", 10.0, 9)


def session_missing() -> CrashEvent:
    return CrashEvent(CrashKind.SESSION_MISSING, "codex", None, 10.0)


def run(coro):
    return asyncio.run(coro)


def test_auto_respawn_schema_defaults_off_and_accepts_explicit_true() -> None:
    default = roster_from_dict(
        {
            "default_greeting_template": "hi {name}",
            "members": [{"name": "a", "command": "true"}],
        }
    )
    enabled = roster_from_dict(
        {
            "default_greeting_template": "hi {name}",
            "members": [{"name": "a", "command": "true", "auto_respawn": True}],
        }
    )
    assert default.members[0].auto_respawn is False
    assert enabled.members[0].auto_respawn is True

    with pytest.raises(ValueError, match="auto_respawn"):
        roster_from_dict(
            {
                "default_greeting_template": "hi {name}",
                "members": [{"name": "a", "command": "true", "auto_respawn": "yes"}],
            }
        )


def test_dead_member_only_alerts_when_auto_respawn_is_off(tmp_path: Path) -> None:
    updates = []
    monitor = FakeMonitor()
    supervisor = HealthSupervisor(
        Roster((member(),)),
        FakeTmux({"codex"}),
        cwd=tmp_path,
        retry_delay=0,
        on_update=updates.append,
    )

    update = run(supervisor.handle_crash("codex", pane_dead(), monitor))

    assert update.state is HealthState.DEAD
    assert "仅告警" in update.detail
    assert monitor.respawn_calls == []
    assert updates[0].state is HealthState.DEAD


def test_dead_pane_is_respawned_in_place_with_member_environment(tmp_path: Path) -> None:
    monitor = FakeMonitor()
    supervisor = HealthSupervisor(
        Roster((member(auto_respawn=True),)),
        FakeTmux({"codex"}),
        cwd=tmp_path,
        retry_delay=0,
    )

    update = run(supervisor.handle_crash("codex", pane_dead(), monitor))

    assert update.state is HealthState.RUNNING
    assert supervisor.consecutive_failures("codex") == 0
    target, command, cwd, env = monitor.respawn_calls[0]
    assert target == "%1"
    assert command.startswith("agent --resume ")
    assert cwd == str(tmp_path)
    assert env == {"LANG": "C", "AGENT_NAME": "codex"}


def test_missing_session_is_recreated_instead_of_respawned(tmp_path: Path) -> None:
    tmux = FakeTmux()
    monitor = FakeMonitor()
    supervisor = HealthSupervisor(
        Roster((member(auto_respawn=True),)), tmux, cwd=tmp_path, retry_delay=0
    )

    update = run(supervisor.handle_crash("codex", session_missing(), monitor))

    assert update.state is HealthState.RUNNING
    assert monitor.respawn_calls == []
    assert tmux.new_calls[0][0] == "codex"
    assert tmux.new_calls[0][1]["env"]["AGENT_NAME"] == "codex"


def test_three_consecutive_failures_enter_failed_and_stop_retrying(tmp_path: Path) -> None:
    updates = []
    monitor = FakeMonitor(failures=99)
    supervisor = HealthSupervisor(
        Roster((member(auto_respawn=True),)),
        FakeTmux({"codex"}),
        cwd=tmp_path,
        retry_delay=0,
        on_update=updates.append,
    )

    update = run(supervisor.handle_crash("codex", pane_dead(), monitor))
    assert update.state is HealthState.FAILED
    assert update.consecutive_failures == 3
    assert len(monitor.respawn_calls) == 3

    again = run(supervisor.handle_crash("codex", pane_dead(), monitor))
    assert again.state is HealthState.FAILED
    assert len(monitor.respawn_calls) == 3
    retries = [item.consecutive_failures for item in updates if "失败，将重试" in item.detail]
    assert retries == [1, 2]


def test_success_after_two_failures_resets_consecutive_count(tmp_path: Path) -> None:
    monitor = FakeMonitor(failures=2)
    supervisor = HealthSupervisor(
        Roster((member(auto_respawn=True),)),
        FakeTmux({"codex"}),
        cwd=tmp_path,
        retry_delay=0,
    )
    update = run(supervisor.handle_crash("codex", pane_dead(), monitor))
    assert update.state is HealthState.RUNNING
    assert len(monitor.respawn_calls) == 3
    assert supervisor.consecutive_failures("codex") == 0


def test_watch_once_uses_crash_monitor_and_timeout(tmp_path: Path) -> None:
    monitor = FakeMonitor(event=pane_dead())
    supervisor = HealthSupervisor(
        Roster((member(),)),
        FakeTmux({"codex"}),
        cwd=tmp_path,
        monitor_factory=lambda _: monitor,
    )
    update = run(supervisor.watch_once("codex", timeout=1.5))
    assert update is not None and update.state is HealthState.DEAD
    assert monitor.wait_calls == [("codex", 1.5)]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")
def test_real_dead_pane_is_detected_and_respawned(tmp_path: Path) -> None:
    socket = f"ros4-{uuid.uuid4().hex[:8]}"
    tmux = Tmux(socket_name=socket)
    name = f"agent-{uuid.uuid4().hex[:6]}"
    member_config = Member(
        name=name,
        command="sh",
        args=("-c", "echo revived; sleep 30"),
        greeting_template="hi {name}",
        auto_respawn=True,
    )
    tmux.new_session(name, command="sleep 30", detached=True, cwd=str(tmp_path))
    supervisor = HealthSupervisor(Roster((member_config,)), tmux, cwd=tmp_path, retry_delay=0)

    async def exercise():
        task = asyncio.create_task(supervisor.watch_once(name, timeout=2.0))
        await asyncio.sleep(0.1)
        pane_pid = int(tmux.display_message(name, "#{pane_pid}").strip())
        os.kill(pane_pid, 9)
        return await task

    try:
        started = time.monotonic()
        update = run(exercise())
        assert time.monotonic() - started < 2.0
        assert update is not None and update.state is HealthState.RUNNING
        assert tmux.display_message(name, "#{pane_dead}").strip() == "0"
        deadline = time.monotonic() + 1.0
        screen = ""
        while "revived" not in screen and time.monotonic() < deadline:
            screen = tmux.capture_pane(name)
            time.sleep(0.02)
        assert "revived" in screen
    finally:
        subprocess.run(tmux.command_argv("kill-server"), capture_output=True)
