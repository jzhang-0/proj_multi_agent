"""CON-006:详情栏的终端画面镜像、100ms 刷新、不可见就停、回滚区翻历史。"""

import asyncio

import pytest

from bus.paths import BusPaths
from console.app import MIRROR_INTERVAL, ConsoleApp
from console.control import ControlFeedback
from console.mirror import HISTORY_LIMIT, HISTORY_STEP, Mirror, terminal_input_rows
from tmuxctl.snapshot import PaneSnapshotter

MEMBERS = ("claude", "codex")


class FakePane:
    """假 tmux:记下每次 capture-pane 的参数,回一段带颜色的画面。"""

    def __init__(self):
        self.calls = []

    def capture_pane(self, target, *, escape=False, start=None, end=None):
        self.calls.append({"target": target, "escape": escape, "start": start})
        where = "回滚区" if start else "当前画面"
        return f"\x1b[32m{target} 的{where}\x1b[0m"


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_app(paths, pane):
    return ConsoleApp(
        paths,
        deliver=lambda message: True,
        members=MEMBERS,
        snapshotter=PaneSnapshotter(pane, min_interval=0),
    )


def run_async(factory):
    return asyncio.run(factory())


async def wait_for(pilot, predicate, rounds=200):
    for _ in range(rounds):
        if predicate():
            return True
        await pilot.pause(0.02)
    return False


def mirror_text(app):
    return app.query_one("#detail", Mirror).screen_text


def test_terminal_input_rows_only_accepts_supported_bottom_composers():
    claude = "输出\n────────────\n❯ 草稿\n\n────────────\n状态\nauto mode"
    codex = "输出\n────────────\n\n› Ask Codex to do anything\n\nmodel · cwd"
    history = "› 历史引用\n正文\n" + "\n".join(f"状态 {i}" for i in range(10))

    assert terminal_input_rows(claude) == (2, 3)
    assert terminal_input_rows(codex) == (3,)
    assert terminal_input_rows(history) == ()


def test_refresh_interval_meets_the_budget():
    assert MIRROR_INTERVAL <= 0.1  # 产品定义:活跃窗格画面刷新 < 100ms


def test_selecting_a_member_mirrors_its_terminal_with_color(paths):
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            assert await wait_for(pilot, lambda: "codex 的当前画面" in mirror_text(app))
            assert pane.calls[-1]["target"] == "codex"
            assert pane.calls[-1]["escape"] is True  # 带颜色抓
            # ANSI 被解析成样式,而不是当成可见字符留在画面上
            assert "\x1b" not in mirror_text(app)

    run_async(scenario)


def test_no_capture_while_the_detail_is_hidden(paths):
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            # 停在群聊会话上:主画面是时间线,一次都不该去问 tmux
            await pilot.pause(MIRROR_INTERVAL * 4)
            assert pane.calls == []

            app.select_member("claude")
            assert await wait_for(pilot, lambda: bool(pane.calls))

            # 切回群聊后也要停:记下当前次数,等几个刷新周期不许再涨
            app.select_member(None)
            await pilot.pause()
            parked = len(pane.calls)
            await pilot.pause(MIRROR_INTERVAL * 4)
            assert len(pane.calls) == parked

    run_async(scenario)


def test_page_up_scrolls_the_members_own_scrollback(paths):
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            mirror = app.query_one("#detail", Mirror)
            assert await wait_for(pilot, lambda: bool(pane.calls))
            assert mirror.capture_start is None

            mirror.focus()
            await pilot.press("pageup")
            await pilot.pause()
            assert mirror.history_offset == HISTORY_STEP
            assert mirror.capture_start == -HISTORY_STEP
            assert await wait_for(pilot, lambda: pane.calls[-1]["start"] == -HISTORY_STEP)
            assert "回滚区" in mirror_text(app)

            await pilot.press("home")
            await pilot.pause()
            assert mirror.history_offset == HISTORY_LIMIT

            await pilot.press("end")
            await pilot.pause()
            assert mirror.history_offset == 0
            assert await wait_for(pilot, lambda: "当前画面" in mirror_text(app))

    run_async(scenario)


def test_detail_is_reachable_by_tab_so_scrollback_is_usable(paths):
    """详情栏能被 Tab 走到,否则"在详情内向上滚动"这条就只有测试能用。"""
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            await pilot.pause()
            for _ in range(6):
                if isinstance(app.focused, Mirror):
                    break
                await pilot.press("tab")
                await pilot.pause()
            assert isinstance(app.focused, Mirror), f"Tab 走不到详情栏,停在 {app.focused}"

    run_async(scenario)


def test_switching_member_returns_to_the_live_screen(paths):
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            mirror = app.query_one("#detail", Mirror)
            mirror.focus()
            await pilot.press("pageup")
            await pilot.pause()
            assert mirror.history_offset == HISTORY_STEP

            app.select_member("claude")
            await pilot.pause()
            assert mirror.history_offset == 0
            assert await wait_for(pilot, lambda: "claude 的当前画面" in mirror_text(app))

    run_async(scenario)


def test_clicking_native_composer_streams_keys_in_order_and_escape_only_exits_live_mode(
    paths,
):
    class ComposerPane(FakePane):
        def __init__(self):
            super().__init__()
            self.draft = ""

        def capture_pane(self, target, *, escape=False, start=None, end=None):
            self.calls.append({"target": target, "escape": escape, "start": start})
            return (
                "成员输出\n"
                "────────────────────────\n"
                f"❯ {self.draft}\n"
                "────────────────────────\n"
                "模型状态\n"
                "auto mode"
            )

    class LiveController:
        def __init__(self, composer):
            self.calls = []
            self.composer = composer

        def insert_text(self, target, text):
            self.calls.append(("text", target, text))
            self.composer.draft += text

        def press_key(self, target, key):
            self.calls.append(("key", target, key))
            return ControlFeedback("key", target, True, key)

        def submit_live_text(self, target, text):
            self.calls.append(("submit", target, text))
            self.composer.draft = ""
            return ControlFeedback("type", target, True, text)

    pane = ComposerPane()
    controller = LiveController(pane)
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=PaneSnapshotter(pane, min_interval=0),
        controller=controller,
        pump_enabled=False,
    )

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            mirror = app.query_one("#detail", Mirror)
            assert await wait_for(pilot, lambda: "auto mode" in mirror.screen_text)
            assert await pilot.click("#detail", offset=(5, 2))
            await pilot.pause()
            assert mirror.live_input and app.focused is mirror
            assert "实时直连 codex" in str(app.query_one("#suggestions").render())

            # q 在实时态是正文，不得触发 App 的全局退出；编辑键与 Enter 保持顺序。
            await pilot.press("h", "i", "q")
            assert await wait_for(pilot, lambda: "❯ hiq" in mirror.screen_text)
            await pilot.press("left", "backspace", "enter")
            assert await wait_for(
                pilot, lambda: any(call[0] == "submit" for call in controller.calls)
            )
            assert app.is_running
            normalized = []
            for call in controller.calls:
                if call[0] == "text" and normalized and normalized[-1][0] == "text":
                    previous = normalized[-1]
                    normalized[-1] = (*previous[:2], previous[2] + call[2])
                else:
                    normalized.append(call)
            assert normalized == [
                ("text", "codex", "hiq"),
                ("key", "codex", "Left"),
                ("key", "codex", "BSpace"),
                ("submit", "codex", "hq"),
            ]

            await pilot.press(
                "tab", "shift+tab", "up", "down", "right", "delete"
            )
            assert await wait_for(pilot, lambda: len(controller.calls) >= 10)
            assert controller.calls[-6:] == [
                ("key", "codex", "Tab"),
                ("key", "codex", "BTab"),
                ("key", "codex", "Up"),
                ("key", "codex", "Down"),
                ("key", "codex", "Right"),
                ("key", "codex", "DC"),
            ]

            await pilot.press("escape")
            await pilot.pause()
            assert not mirror.live_input
            assert app.selected_member == "codex"  # 没有被全局 Esc 切回工作对话

    run_async(scenario)


def test_clicking_output_or_scrollback_does_not_activate_live_input(paths):
    pane = FakePane()
    app = make_app(paths, pane)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            await pilot.pause()
            mirror = app.query_one("#detail", Mirror)
            mirror.show_screen(
                "普通输出\n────────────────\n❯ \n────────────────\n状态\nauto mode"
            )
            await pilot.click("#detail", offset=(3, 0))
            assert not mirror.live_input

            mirror.scroll_history(3)
            await pilot.click("#detail", offset=(3, 2))
            assert not mirror.live_input

    run_async(scenario)
