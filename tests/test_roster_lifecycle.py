"""ROS-003:生命周期 up/down/restart 的幂等性,以及真起一个会话验证注入。

逻辑分支用假 tmux 跑(快、不碰真实会话);`AGENT_NAME` 注入和开场白
是否真的到了终端里,用一个临时 tmux 会话实测一次。
"""

import shutil
import subprocess
import uuid

import pytest

from roster.lifecycle import Action, Lifecycle
from roster.schema import Member, Roster, RosterError


class FakeTmux:
    """记录调用的假 tmux;`sessions` 就是"当前在跑的会话"。"""

    def __init__(self, sessions=()):
        self.sessions = set(sessions)
        self.calls = []

    def has_session(self, name):
        return name in self.sessions

    def new_session(self, name, **kwargs):
        if name in self.sessions:
            raise AssertionError(f"重复拉起 {name}")
        self.sessions.add(name)
        self.calls.append(("new", name, kwargs))

    def kill_session(self, name, missing_ok=False):
        self.sessions.discard(name)
        self.calls.append(("kill", name, {}))


def roster_of(*names, disabled=()):
    members = tuple(
        Member(
            name=name,
            command="sh",
            args=("-c", "true"),
            greeting_template="你好 {name}",
            enabled=name not in disabled,
        )
        for name in names
    )
    return Roster(members=members)


@pytest.fixture
def lifecycle():
    return Lifecycle(roster_of("claude", "codex"), FakeTmux())


# --- 幂等 ---------------------------------------------------------------


def test_up_starts_everyone_then_does_nothing_the_second_time(lifecycle):
    first = lifecycle.up()
    assert [(r.name, r.changed) for r in first] == [("claude", True), ("codex", True)]

    second = lifecycle.up()
    assert [r.changed for r in second] == [False, False]
    assert all("已在运行" in r.detail for r in second)
    assert [call[0] for call in lifecycle.tmux.calls] == ["new", "new"]


def test_down_is_idempotent(lifecycle):
    lifecycle.up()
    assert [r.changed for r in lifecycle.down()] == [True, True]
    assert [r.changed for r in lifecycle.down()] == [False, False]
    assert lifecycle.tmux.sessions == set()


def test_restart_kills_then_starts_and_works_on_a_stopped_member(lifecycle):
    lifecycle.up("claude")
    result = lifecycle.restart("claude")[0]
    assert (result.action, result.changed) == (Action.RESTART, True)
    assert [call[0] for call in lifecycle.tmux.calls] == ["new", "kill", "new"]

    lifecycle.tmux.calls.clear()
    stopped = lifecycle.restart("codex")[0]
    assert stopped.changed and "已拉起" in stopped.detail
    assert [call[0] for call in lifecycle.tmux.calls] == ["new"]


def test_single_member_actions_only_touch_that_member(lifecycle):
    lifecycle.up("claude")
    assert lifecycle.tmux.sessions == {"claude"}
    assert lifecycle.running() == ("claude",)


def test_unknown_member_is_rejected(lifecycle):
    with pytest.raises(RosterError, match="未知成员"):
        lifecycle.up("nobody")


def test_disabled_member_is_skipped_by_up_but_still_stoppable():
    life = Lifecycle(roster_of("claude", "agy", disabled={"agy"}), FakeTmux(sessions={"agy"}))
    assert [(r.name, r.changed) for r in life.up()] == [("claude", True)]

    skipped = life.up("agy")[0]
    assert not skipped.changed and "已停用" in skipped.detail

    # 停用成员留下的残留会话,down 全体时照样收掉
    assert ("agy", True) in [(r.name, r.changed) for r in life.down()]


# --- 启动时真的注入了 AGENT_NAME 和开场白 --------------------------------


@pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")
def test_real_session_gets_agent_name_and_greeting():
    from tmuxctl import Tmux

    name = f"ros3-{uuid.uuid4().hex[:6]}"
    member = Member(
        name=name,
        command="sh",
        args=("-c", 'echo "AGENT=$AGENT_NAME GREETING=$0"; cat'),
        greeting_template="你好 {name},先读 AGENTS.md",
    )
    life = Lifecycle(Roster(members=(member,)), Tmux())
    try:
        assert life.up(name)[0].changed
        subprocess.run(["sleep", "0.3"], check=True)
        screen = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert f"AGENT={name}" in screen
        assert f"GREETING=你好 {name},先读 AGENTS.md" in screen
        assert not life.up(name)[0].changed  # 幂等:不会把正在跑的顶掉
        assert life.down(name)[0].changed
        assert life.running() == ()
    finally:
        subprocess.run(["tmux", "kill-session", "-t", f"={name}"], capture_output=True)
