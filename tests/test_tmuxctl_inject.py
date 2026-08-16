"""TMX-002: 按键注入与半行字隔离。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import KeyInjector, Tmux, cursor_line_holds, last_line_uncommitted


class FakeTmux:
    def __init__(self, snapshots: list[str] | str) -> None:
        self._snapshots = [snapshots] if isinstance(snapshots, str) else list(snapshots)
        self.calls: list[tuple[str, tuple[str, ...], bool]] = []
        #: (画面, 光标行) 序列,给 capture_with_cursor 用
        self.cursor_views: list[tuple[str, int]] = []

    def capture_pane(self, target: str, **_kwargs: object) -> str:
        if not self._snapshots:
            return "$ "
        if len(self._snapshots) == 1:
            return self._snapshots[0]
        return self._snapshots.pop(0)

    def capture_with_cursor(self, target: str) -> tuple[str, int]:
        if not self.cursor_views:
            return "$ ", 0
        if len(self.cursor_views) == 1:
            return self.cursor_views[0]
        return self.cursor_views.pop(0)

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None:
        self.calls.append((target, keys, literal))

    def send_line(self, target: str, text: str) -> None:
        self.calls.append((target, (text, "Enter"), True))


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


MSG = '[群消息] 来自 human: 请核对 README —— 如需回复,运行: amux msg human "你的回复"'

# 取自真实 cursor 窗格:输入框在上、状态栏常驻在下,消息卡在输入框里没提交。
CURSOR_STUCK = "\n".join(
    [
        "    上一条对话的收尾 —— 如需回复,运行: ./msg im:验收人 \"你的回复\"",
        f"    {MSG}",
        "",
        "  Cursor Grok 4.6 High  ctx 35.6k / 256.0k  14%",
        "  proj_multi_agent · main",
    ]
)
# 同一条消息提交成功:字进了对话区,光标回到空的输入行。
CURSOR_SUBMITTED = "\n".join(
    [
        f"    {MSG}",
        "",
        "",
        "  Cursor Grok 4.6 High  ctx 35.6k / 256.0k  14%",
        "  proj_multi_agent · main",
    ]
)


def test_cursor_line_holds_detects_stuck_input() -> None:
    assert cursor_line_holds(CURSOR_STUCK, 1, MSG) is True


def test_cursor_line_holds_ignores_transcript_echo() -> None:
    """提交后的回显也在画面上,但不在光标行,不能当成没提交。"""
    assert cursor_line_holds(CURSOR_SUBMITTED, 2, MSG) is False


def test_cursor_line_holds_catches_newline_in_composer() -> None:
    """Enter 被当成输入框内换行:字在上一行,光标停在紧邻的空行。"""
    snapshot = f"    {MSG}\n\n  状态栏"
    assert cursor_line_holds(snapshot, 1, MSG) is True


def test_cursor_line_holds_out_of_range() -> None:
    assert cursor_line_holds("$ ", 99, MSG) is False


def test_deliver_is_one_tmux_call() -> None:
    tmux = FakeTmux("$ ")
    KeyInjector(tmux).deliver("claude", MSG)
    assert tmux.calls == [("claude", (MSG, "Enter"), True)]


def test_ensure_submitted_quiet_when_committed() -> None:
    clock = FakeClock()
    tmux = FakeTmux("$ ")
    tmux.cursor_views = [(CURSOR_SUBMITTED, 2)]
    outcome = KeyInjector(tmux, clock=clock.now, sleeper=clock.sleep).ensure_submitted(
        "cursor", MSG
    )
    assert (outcome.submitted, outcome.retries) == (True, 0)
    assert tmux.calls == []


def test_ensure_submitted_presses_enter_until_committed() -> None:
    clock = FakeClock()
    tmux = FakeTmux("$ ")
    tmux.cursor_views = [(CURSOR_STUCK, 1), (CURSOR_SUBMITTED, 2)]
    outcome = KeyInjector(tmux, clock=clock.now, sleeper=clock.sleep).ensure_submitted(
        "cursor", MSG
    )
    assert (outcome.submitted, outcome.retries) == (True, 1)
    assert tmux.calls == [("cursor", ("Enter",), False)]


def test_ensure_submitted_gives_up_and_reports() -> None:
    clock = FakeClock()
    tmux = FakeTmux("$ ")
    tmux.cursor_views = [(CURSOR_STUCK, 1)]
    outcome = KeyInjector(tmux, clock=clock.now, sleeper=clock.sleep).ensure_submitted(
        "cursor", MSG, retries=2
    )
    assert (outcome.submitted, outcome.retries) == (False, 2)
    assert tmux.calls == [("cursor", ("Enter",), False)] * 2


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


def _wait_idle(tmux: Tmux, name: str) -> None:
    for _ in range(20):
        snapshot = tmux.capture_pane(name)
        if snapshot.strip() and not last_line_uncommitted(snapshot):
            return
        time.sleep(0.05)


def test_real_tmux_deliver_and_confirm(isolated_tmux: Tmux) -> None:
    """一次调用注入并提交;确认这一步看不出问题,不该乱补 Enter。"""
    name = f"sess-{uuid.uuid4().hex[:6]}"
    isolated_tmux.new_session(
        name, command=["bash", "--norc", "--noprofile"], env={"PS1": "$ ", "TERM": "dumb"}
    )
    try:
        _wait_idle(isolated_tmux, name)
        injector = KeyInjector(isolated_tmux)
        injector.deliver(name, "echo DELIVERED")
        outcome = injector.ensure_submitted(name, "echo DELIVERED")
        assert (outcome.submitted, outcome.retries) == (True, 0)
        pane = isolated_tmux.capture_pane(name)
        assert "DELIVERED" in pane
    finally:
        isolated_tmux.kill_session(name, missing_ok=True)


def test_real_tmux_confirm_rescues_swallowed_enter(isolated_tmux: Tmux) -> None:
    """复现 GATE-003 的缺陷:字进了输入行、Enter 没生效,确认这一步要补上。"""
    name = f"sess-{uuid.uuid4().hex[:6]}"
    isolated_tmux.new_session(
        name, command=["bash", "--norc", "--noprofile"], env={"PS1": "$ ", "TERM": "dumb"}
    )
    try:
        _wait_idle(isolated_tmux, name)
        # 只发文本不发 Enter = 那一下 Enter 被成员 CLI 吞掉的现场
        isolated_tmux.send_keys(name, "echo RESCUED", literal=True)
        snapshot, cursor_y = isolated_tmux.capture_with_cursor(name)
        assert cursor_line_holds(snapshot, cursor_y, "echo RESCUED") is True
        outcome = KeyInjector(isolated_tmux).ensure_submitted(name, "echo RESCUED")
        assert outcome.submitted is True
        assert outcome.retries == 1
        time.sleep(0.15)
        assert "RESCUED" in isolated_tmux.capture_pane(name)
    finally:
        isolated_tmux.kill_session(name, missing_ok=True)
