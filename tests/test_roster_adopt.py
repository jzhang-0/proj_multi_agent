"""ROS-005:发现并临时收编静态 roster 外的 tmux 会话。"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Iterator

import pytest

from bus import Message
from bus.sanitize import format_for_injection
from roster.adopt import SessionAdopter
from roster.schema import Member, Roster, RosterError
from tmuxctl import PaneInfo, Tmux, TmuxCommandError


def pane(name: str, pane_id: str, *, window: int = 0, command: str = "cat") -> PaneInfo:
    return PaneInfo(name, window, 0, pane_id, 1000 + window, command)


class FakeTmux:
    def __init__(self, panes=()):
        self.panes = list(panes)

    def list_panes(self, target=None, *, all_sessions=False):
        assert target is None
        assert all_sessions is True
        return list(self.panes)

    def has_session(self, name):
        return any(item.session_name == name for item in self.panes)


def roster() -> Roster:
    return Roster(
        (
            Member("claude", "claude", greeting_template="hi"),
            Member("disabled", "true", greeting_template="hi", enabled=False),
        )
    )


def test_discover_only_lists_valid_unconfigured_sessions_in_stable_order() -> None:
    tmux = FakeTmux(
        [
            pane("zeta", "%4", window=1, command="bash"),
            pane("claude", "%1"),
            pane("alpha", "%3", command="agent"),
            pane("zeta", "%2", window=0, command="vim"),
            pane("disabled", "%5"),
            pane("human", "%6"),
            pane("bad name", "%7"),
        ]
    )
    adopter = SessionAdopter(roster(), tmux)

    candidates = adopter.discover()

    assert [item.name for item in candidates] == ["alpha", "zeta"]
    assert candidates[1].pane_ids == ("%2", "%4")
    assert candidates[1].commands == ("vim", "bash")


def test_adopt_is_idempotent_and_exposes_recipient_and_timeline_names() -> None:
    tmux = FakeTmux([pane("claude", "%1"), pane("legacy", "%2", command="codex")])
    adopter = SessionAdopter(roster(), tmux)

    adopted = adopter.adopt("legacy")
    same = adopter.adopt("legacy")

    assert same is adopted
    assert adopted.temporary is True
    assert adopted.commands == ("codex",)
    assert adopter.member_names() == ("claude", "legacy")
    assert adopter.is_member("legacy")
    assert adopter.can_receive("legacy")
    assert adopter.discover() == ()

    message = Message.create("legacy", "ping", "human")
    assert adopter.is_member(message.to)  # 收件人补全/成员栏/时间线共用同一名称集合


def test_adoption_does_not_persist_across_directory_instances() -> None:
    tmux = FakeTmux([pane("legacy", "%1")])
    first = SessionAdopter(roster(), tmux)
    first.adopt("legacy")

    restarted = SessionAdopter(roster(), tmux)
    assert restarted.adopted_members() == ()
    assert [candidate.name for candidate in restarted.discover()] == ["legacy"]


def test_forget_only_drops_temporary_record_and_does_not_kill_session() -> None:
    tmux = FakeTmux([pane("legacy", "%1")])
    adopter = SessionAdopter(roster(), tmux)
    adopter.adopt("legacy")

    assert adopter.forget("legacy") is True
    assert adopter.forget("legacy") is False
    assert tmux.has_session("legacy")
    assert [candidate.name for candidate in adopter.discover()] == ["legacy"]


def test_adopt_rejects_configured_missing_and_reserved_names() -> None:
    adopter = SessionAdopter(roster(), FakeTmux([pane("claude", "%1")]))
    with pytest.raises(RosterError, match="已在 roster.toml"):
        adopter.adopt("claude")
    with pytest.raises(RosterError, match="找不到可收编"):
        adopter.adopt("missing")
    with pytest.raises(RosterError, match="保留名"):
        adopter.adopt("human")


def test_no_tmux_server_means_no_candidates() -> None:
    class NoServer(FakeTmux):
        def list_panes(self, target=None, *, all_sessions=False):
            raise TmuxCommandError(["tmux", "list-panes"], 1, "no server running")

    assert SessionAdopter(roster(), NoServer()).discover() == ()


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"ros5-{uuid.uuid4().hex[:8]}")
    try:
        yield client
    finally:
        client.kill_server()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")
def test_real_existing_session_can_be_adopted_and_receive_message(isolated_tmux: Tmux) -> None:
    configured = f"configured-{uuid.uuid4().hex[:5]}"
    legacy = f"legacy-{uuid.uuid4().hex[:5]}"
    isolated_tmux.new_session(configured, command="cat")
    isolated_tmux.new_session(legacy, command="cat")
    static = Roster((Member(configured, "cat", greeting_template="hi"),))
    adopter = SessionAdopter(static, isolated_tmux)

    assert [candidate.name for candidate in adopter.discover()] == [legacy]
    adopter.adopt(legacy)
    assert adopter.can_receive(legacy)

    message = Message.create(legacy, "收编成功", "human")
    line = format_for_injection(message)
    isolated_tmux.send_keys(legacy, line, literal=True)
    isolated_tmux.send_keys(legacy, "Enter")
    deadline = time.monotonic() + 1.0
    screen = ""
    while "收编成功" not in screen and time.monotonic() < deadline:
        screen = isolated_tmux.capture_pane(legacy)
        time.sleep(0.02)
    assert "收编成功" in screen

    # 收编与遗忘都不结束用户原有会话。
    assert adopter.forget(legacy)
    assert isolated_tmux.has_session(legacy)
