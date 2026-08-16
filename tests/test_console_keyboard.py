"""CON-012:纯键盘覆盖、快捷键帮助与焦点循环。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.cells import cell_len
from textual.widgets import ListView

from bus import BusPaths
from console.app import ConsoleApp
from console.compose import ComposeInput
from console.help import SHORTCUT_GROUPS, ShortcutHelpScreen, render_shortcuts
from console.members import MemberStatusService
from console.mirror import HISTORY_STEP, Mirror
from console.widgets import Timeline
from tmuxctl import PaneSnapshot


class FakeSnapshotter:
    async def capture(self, target, *, color=False, start=None):
        return PaneSnapshot(target, "screen", color, start, None, 0.0)


def make_app(tmp_path: Path) -> ConsoleApp:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    return ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=FakeSnapshotter(),
        member_status=MemberStatusService(("codex",)),
        pump_enabled=False,
    )


def test_focus_cycles_forward_and_backward_through_all_work_areas(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            # 一起来焦点在会话列表,主画面是群聊时间线
            assert isinstance(app.focused, ListView)
            assert not app.detail_visible

            forward = []
            for _ in range(3):
                await pilot.press("tab")
                forward.append(type(app.focused))
            assert forward == [Timeline, ComposeInput, ListView]

            backward = []
            for _ in range(3):
                await pilot.press("shift+tab")
                backward.append(type(app.focused))
            assert backward == [ComposeInput, Timeline, ListView]

            # 选中成员后主画面换成它的终端画面,循环里的主画面也跟着换
            await pilot.press("down")
            await pilot.pause()
            assert app.detail_visible
            await pilot.press("tab")
            assert isinstance(app.focused, Mirror)

            # 详情的键盘路径不只是“能聚焦”，实际可翻回滚区。
            await pilot.press("pageup")
            assert app.query_one("#detail", Mirror).history_offset == HISTORY_STEP

    asyncio.run(scenario())


def test_question_mark_opens_help_and_escape_restores_focus(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            members = app.query_one("#members", ListView)
            assert app.focused is members
            await pilot.press("question_mark")
            assert isinstance(app.screen, ShortcutHelpScreen)
            assert app.screen.query_one("#shortcut-help-content") is not None
            scroll = app.screen.query_one("#shortcut-help-scroll")
            assert scroll.scroll_y == 0
            await pilot.press("end")
            assert scroll.scroll_y > 0
            await pilot.press("home")
            assert scroll.scroll_y == 0
            await pilot.press("escape")
            assert not isinstance(app.screen, ShortcutHelpScreen)
            assert app.focused is members

            # 输入框里的问号仍是正文；输入时可用全局 F1 看帮助。
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            await pilot.press("question_mark")
            assert compose.value == "?"
            await pilot.press("f1")
            assert isinstance(app.screen, ShortcutHelpScreen)
            await pilot.press("f1")
            assert app.focused is compose
            assert compose.value == "?"

    asyncio.run(scenario())


def test_help_documents_every_console_binding_and_context_action() -> None:
    keys = {shortcut.keys for _, shortcuts in SHORTCUT_GROUPS for shortcut in shortcuts}
    assert {"F5", "F6", "F7", "F8", "? / F1", "T", "Q / Ctrl+C"} <= keys
    text = render_shortcuts().plain
    for capability in (
        "Tab / Shift+Tab",
        "PgUp / PgDn",
        "@ 成员",
        "/up /down /restart /adopt /mute /help",
        "Y 确认 / N 取消",
        "退出 attach 后返回",
    ):
        assert capability in text

    # 键名里有中文双宽字符，说明列仍必须从同一终端列开始。
    for _, shortcuts in SHORTCUT_GROUPS:
        for shortcut in shortcuts:
            line = next(line for line in text.splitlines() if shortcut.description in line)
            prefix = line[: line.index(shortcut.description)]
            assert cell_len(prefix) == 2 + 20
