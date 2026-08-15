"""CON-008:`/` 命令面板——六条命令、补全、错误提示、静音真的挡消息。"""

import asyncio

import pytest
from textual.widgets import Static

from bus import Message, deposit
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.buspump import MutePolicy
from console.commands import COMMAND_NAMES, CommandRunner, matching_commands, parse_command
from console.compose import ComposeInput
from console.widgets import Timeline

MEMBERS = ("claude", "codex", "cursor", "agy")


class FakeLifecycle:
    """只记调用的生命周期替身。"""

    def __init__(self):
        self.calls = []

    def _result(self, action, name):
        class _R:
            def line(self_inner):
                return f"[{action}] {name} 假装做完了"

        self.calls.append((action, name))
        return [_R()]

    def up(self, name=None):
        return self._result("up", name)

    def down(self, name=None):
        return self._result("down", name)

    def restart(self, name=None):
        return self._result("restart", name)


class FakeAdopter:
    def __init__(self):
        self.adopted = []

    def adopt(self, name):
        if name == "查无此会话":
            raise RuntimeError(f"找不到可收编的 tmux 会话: {name}")
        self.adopted.append(name)

        class _M:
            pass

        member = _M()
        member.name = name
        return member

    def member_names(self):
        return (*MEMBERS, *self.adopted)


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_runner():
    return CommandRunner(lifecycle=FakeLifecycle(), adopter=FakeAdopter())


def run_async(factory):
    return asyncio.run(factory())


# --- 解析与补全 ---------------------------------------------------------


def test_parse_and_complete():
    assert parse_command("/up claude") == ("up", ["claude"])
    assert parse_command("/help") == ("help", [])
    assert matching_commands("re") == ("restart",)
    assert set(matching_commands("")) == set(COMMAND_NAMES)


def test_help_lists_every_command_aligned_by_display_width():
    from console.commands import display_width, help_lines

    lines = "\n".join(make_runner().run("/help"))
    for name in COMMAND_NAMES:
        assert f"/{name}" in lines
    # 中文是双宽:说明列必须落在同一列上,否则 /help 那行会错位
    from console.commands import COMMANDS

    starts = {
        display_width(line[: line.rindex(spec.help)])
        for spec, line in zip(COMMANDS, help_lines(), strict=True)
    }
    assert len(starts) == 1, f"帮助的说明列没对齐:{starts}"


# --- 六条命令 -----------------------------------------------------------


@pytest.mark.parametrize("action", ["up", "down", "restart"])
def test_lifecycle_commands_call_roster(action):
    runner = make_runner()
    out = runner.run(f"/{action} codex")
    assert runner.lifecycle.calls == [(action, "codex")]
    assert out == [f"[{action}] codex 假装做完了"]


def test_adopt_reports_temporary_membership():
    runner = make_runner()
    assert "已收编为临时成员" in runner.run("/adopt alice")[0]
    assert runner.adopter.adopted == ["alice"]


def test_mute_toggles():
    runner = make_runner()
    assert "已静音" in runner.run("/mute agy")[0]
    assert runner.muted == {"agy"}
    assert "取消静音" in runner.run("/mute agy")[0]
    assert runner.muted == set()


# --- 错误提示 -----------------------------------------------------------


def test_unknown_command_suggests_the_closest_one():
    assert "你是不是想用 /up" in make_runner().run("/uo claude")[0]


def test_missing_argument_shows_usage():
    assert make_runner().run("/up") == ["用法:/up <名字> —— 拉起成员(已在跑的不动它)"]


def test_backend_failure_becomes_one_line():
    assert "找不到可收编" in make_runner().run("/adopt 查无此会话")[0]


def test_commands_are_unavailable_without_roster():
    bare = CommandRunner()
    assert "不可用" in bare.run("/up claude")[0]
    assert "不可用" in bare.run("/adopt alice")[0]


# --- 接进界面 -----------------------------------------------------------


def test_slash_completes_commands_in_the_input(paths):
    app = ConsoleApp(paths, deliver=lambda m: True, members=MEMBERS)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            await pilot.press("/", "u")
            await pilot.pause()
            assert compose.candidates == ("up",)
            assert compose.candidate_kind == "command"
            assert "/up" in str(app.query_one("#suggestions", Static).render())

            await pilot.press("tab")
            await pilot.pause()
            assert compose.value == "/up "

    run_async(scenario)


def test_command_output_goes_to_the_timeline_not_the_bus(paths):
    sent = []
    app = ConsoleApp(paths, deliver=lambda m: sent.append(m) is None, members=MEMBERS)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.commands.lifecycle = FakeLifecycle()
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "/restart codex"
            await pilot.press("enter")
            await pilot.pause()

            log = app.query_one("#timeline", Timeline)
            lines = ["".join(segment.text for segment in line) for line in log.lines]
            assert any("/restart codex" in line for line in lines)
            assert any("假装做完了" in line for line in lines)
            assert sent == []  # 命令不发消息
            assert compose.value == ""

    run_async(scenario)


def test_muted_member_messages_are_rejected(paths):
    delivered = []
    app = ConsoleApp(paths, deliver=lambda m: delivered.append(m) is None, members=MEMBERS)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "/mute agy"
            await pilot.press("enter")
            await pilot.pause()
            assert app.muted == {"agy"}

            deposit(Message.create("claude", "被静音的一条", sender="agy"), paths)
            deposit(Message.create("claude", "正常的一条", sender="codex"), paths)
            for _ in range(200):
                if any(m.text == "正常的一条" for m in delivered):
                    break
                await pilot.pause(0.02)
            assert [m.text for m in delivered if m.sender != "bus"] == ["正常的一条"]

    run_async(scenario)


def test_mute_policy_defers_to_the_bus_rules():
    muted = {"agy"}
    policy = MutePolicy(muted)
    blocked = policy.check(Message.create("claude", "拦下来", sender="agy"))
    assert not blocked.ok and "静音" in blocked.reason
    assert policy.check(Message.create("claude", "放行", sender="codex")).ok
