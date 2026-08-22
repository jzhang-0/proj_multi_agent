"""CON-007:键盘控制、危险操作确认、接管挂起与完整审计。"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from pathlib import Path

import pytest

from bus import BusPaths
from bus.audit import AuditLog
from console.app import ConsoleApp
from console.control import ConfirmControlScreen, ControlFeedback, MemberController
from console.members import MemberStatusService
from console.widgets import Timeline
from roster.lifecycle import Action, LifecycleResult
from tmuxctl import ControlAction, ControlResult, PaneSnapshot


class FakeTmux:
    def __init__(self, exists=True):
        self.exists = exists

    def has_session(self, name):
        return self.exists

    def command_argv(self, *args):
        return ["tmux-test", "-L", "isolated", *args]


class FakeProcess:
    def __init__(self):
        self.calls = []

    def interrupt(self, target):
        self.calls.append(("interrupt", target))
        return ControlResult(ControlAction.INTERRUPT, target, True)

    def terminate(self, target):
        self.calls.append(("terminate", target))
        return ControlResult(ControlAction.TERMINATE, target, True, 4321)


class FakeLifecycle:
    def __init__(self):
        self.calls = []

    def restart(self, target):
        self.calls.append(target)
        return [LifecycleResult(target, Action.RESTART, True, "已重启")]


class FakeSnapshotter:
    async def capture(self, target, *, color=False, start=None):
        return PaneSnapshot(target, "screen", color, start, None, 0.0)


def make_controller(paths, *, exists=True, returncode=0):
    tmux = FakeTmux(exists)
    process = FakeProcess()
    lifecycle = FakeLifecycle()
    runs = []

    def runner(argv, *, check=False):
        runs.append((argv, check))
        return subprocess.CompletedProcess(argv, returncode)

    controller = MemberController(
        tmux,
        lifecycle,
        AuditLog(paths),
        process=process,
        runner=runner,
    )
    return controller, process, lifecycle, runs


def test_controller_delegates_all_actions_and_audits_them(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    controller, process, lifecycle, runs = make_controller(paths)

    assert controller.interrupt("codex").changed
    assert controller.terminate("codex").changed
    assert controller.restart("codex").changed
    assert controller.takeover("codex").changed

    assert process.calls == [("interrupt", "codex"), ("terminate", "codex")]
    assert lifecycle.calls == ["codex"]
    assert runs == [
        (["tmux-test", "-L", "isolated", "attach-session", "-t", "=codex"], False)
    ]
    entries = AuditLog(paths).entries()
    assert [entry["event"] for entry in entries] == ["control"] * 4
    assert [entry["action"] for entry in entries] == [
        "interrupt",
        "terminate",
        "restart",
        "takeover",
    ]
    assert all(entry["from"] == "human" and entry["to"] == "codex" for entry in entries)
    assert all(entry["changed"] is True for entry in entries)


def test_typed_text_and_its_enter_are_two_calls_with_a_gap(tmp_path: Path) -> None:
    """文本和 Enter 挤在一次 tmux 调用里,cursor-agent 会当成粘贴——只换行不提交。

    2026-08-16 实测:它的输入框里压着一条 14:58 的群消息和后来键入的 `11111`,
    补一个单独的 Enter 两条一起提交了。所以这里钉住"分两次发 + 中间等一下"。
    """
    paths = BusPaths.resolve(tmp_path / "bus").ensure()

    class RecordingTmux(FakeTmux):
        def __init__(self):
            super().__init__()
            self.calls: list[tuple] = []

        def send_keys(self, target, *keys, literal=False):
            self.calls.append(("send_keys", target, keys, literal))

        def capture_with_cursor(self, target):
            self.calls.append(("capture", target))
            return "提示符 ❯", 0  # 光标不在那行字上 → 判定已提交

    tmux = RecordingTmux()
    slept: list[float] = []
    controller = MemberController(
        tmux, FakeLifecycle(), AuditLog(paths), sleeper=slept.append
    )

    feedback = controller.type_text("cursor", "继续做 GATE-004")

    assert feedback.changed
    assert tmux.calls[0] == ("send_keys", "cursor", ("继续做 GATE-004",), True)
    assert tmux.calls[1] == ("send_keys", "cursor", ("Enter",), False)
    assert slept and slept[0] >= 0.1  # 两次之间真的等了一下
    entry = AuditLog(paths).entries()[-1]
    assert entry["action"] == "type" and entry["to"] == "cursor"


def test_controller_passes_allowed_direct_keys_and_audits_them(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()

    class RecordingTmux(FakeTmux):
        def __init__(self):
            super().__init__()
            self.calls: list[tuple] = []

        def send_keys(self, target, *keys, literal=False):
            self.calls.append((target, keys, literal))

    tmux = RecordingTmux()
    controller = MemberController(tmux, FakeLifecycle(), AuditLog(paths))

    assert controller.press_key("claude", "BTab").changed
    assert controller.press_key("claude", "Enter").changed
    assert controller.press_key("claude", "BSpace").changed
    assert controller.press_key("claude", "DC").changed
    assert tmux.calls == [
        ("claude", ("BTab",), False),
        ("claude", ("Enter",), False),
        ("claude", ("BSpace",), False),
        ("claude", ("DC",), False),
    ]
    assert [(entry["action"], entry["reason"]) for entry in AuditLog(paths).entries()] == [
        ("key", "BTab"),
        ("key", "Enter"),
        ("key", "BSpace"),
        ("key", "DC"),
    ]
    with pytest.raises(ValueError, match="不允许"):
        controller.press_key("claude", "C-c")


def test_missing_takeover_target_and_controller_error_are_audited(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    controller, _, _, runs = make_controller(paths, exists=False)
    feedback = controller.takeover("dead")
    assert not feedback.changed and runs == []

    class BrokenProcess(FakeProcess):
        def interrupt(self, target):
            raise RuntimeError("control broke")

    broken = MemberController(
        FakeTmux(),
        FakeLifecycle(),
        AuditLog(paths),
        process=BrokenProcess(),
    )
    with pytest.raises(RuntimeError, match="control broke"):
        broken.interrupt("codex")

    entries = AuditLog(paths).entries()
    assert [(entry["action"], entry["changed"]) for entry in entries] == [
        ("takeover", False),
        ("interrupt", False),
    ]
    assert "RuntimeError" in entries[-1]["reason"]


class RecordingController:
    def __init__(self):
        self.calls = []

    def _call(self, action, target):
        self.calls.append((action, target))
        return ControlFeedback(action, target, True, "ok")

    def interrupt(self, target):
        return self._call("interrupt", target)

    def terminate(self, target):
        return self._call("terminate", target)

    def restart(self, target):
        return self._call("restart", target)

    def takeover(self, target):
        return self._call("takeover", target)

    def record_failure(self, action, target, exc):
        self.calls.append((f"failed-{action}", target))


def timeline_text(app) -> str:
    timeline = app.query_one("#timeline", Timeline)
    return "\n".join("".join(segment.text for segment in line) for line in timeline.lines)


def test_keyboard_actions_require_selection_and_confirm_dangerous_operations(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    controller = RecordingController()
    service = MemberStatusService(("codex",))
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=FakeSnapshotter(),
        member_status=service,
        pump_enabled=False,
        controller=controller,
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("f5")
            assert "先在成员栏选择" in timeline_text(app)
            assert controller.calls == []

            await pilot.press("down")  # 会话列表:群聊下面第一个成员
            await pilot.press("f5")
            assert controller.calls == [("interrupt", "codex")]

            await pilot.press("f6")
            assert isinstance(app.screen, ConfirmControlScreen)
            assert app.focused is not None and app.focused.id == "confirm-no"
            await pilot.press("n")
            assert controller.calls == [("interrupt", "codex")]

            await pilot.press("f6")
            await pilot.press("y")
            assert controller.calls[-1] == ("terminate", "codex")

            await pilot.press("f7")
            await pilot.press("escape")
            assert not any(action == "restart" for action, _ in controller.calls)
            await pilot.press("f7")
            await pilot.press("y")
            assert controller.calls[-1] == ("restart", "codex")

    asyncio.run(scenario())


def test_takeover_suspends_console_then_returns_and_all_actions_have_bindings(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    controller = RecordingController()
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=FakeSnapshotter(),
        member_status=MemberStatusService(("codex",)),
        pump_enabled=False,
        controller=controller,
    )
    suspends = []

    @contextlib.contextmanager
    def suspended():
        suspends.append("enter")
        yield
        suspends.append("exit")

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            app.suspend = suspended  # type: ignore[method-assign]
            await pilot.press("f8")
            assert suspends == ["enter", "exit"]
            assert controller.calls == [("takeover", "codex")]
            assert "已回到总控台" not in timeline_text(app)  # fake feedback 仍明确显示 ok
            assert "takeover codex: ok" in timeline_text(app)

    asyncio.run(scenario())
    bindings = {binding.key: binding.action for binding in ConsoleApp.BINDINGS}
    assert {bindings[key] for key in ("f5", "f6", "f7", "f8")} == {
        "interrupt_selected",
        "terminate_selected",
        "restart_selected",
        "takeover_selected",
    }
