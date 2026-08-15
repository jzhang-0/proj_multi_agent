"""CON-006:详情栏的终端画面镜像、100ms 刷新、不可见就停、回滚区翻历史。"""

import asyncio

import pytest

from bus.paths import BusPaths
from console.app import MIRROR_INTERVAL, ConsoleApp
from console.mirror import HISTORY_LIMIT, HISTORY_STEP, Mirror
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
            # 没选中成员:详情栏折叠,一次都不该去问 tmux
            await pilot.pause(MIRROR_INTERVAL * 4)
            assert pane.calls == []

            app.select_member("claude")
            assert await wait_for(pilot, lambda: bool(pane.calls))

            # 窄屏让位后也要停:记下当前次数,等几个刷新周期不许再涨
            await pilot.resize_terminal(80, 24)
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
