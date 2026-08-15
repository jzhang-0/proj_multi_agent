"""CON-002:三栏布局、详情栏折叠与让位、80×24 可用。

画面本身另外看图确认(证据在 Goal 里),这里钉住尺寸与显隐规则。
"""

import asyncio

import pytest
from textual.widgets import Input, ListView, RichLog, Static

from bus.paths import BusPaths
from console.app import DETAIL_MIN_WIDTH, MIN_SIZE, ConsoleApp

MEMBERS = ("claude", "codex", "cursor", "agy")


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_app(paths):
    return ConsoleApp(paths, deliver=lambda message: True, members=MEMBERS)


def run_async(coro_factory):
    return asyncio.run(coro_factory())


def test_three_columns_and_input_are_all_there(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)):
            members = app.query_one("#members", ListView)
            timeline = app.query_one("#timeline", RichLog)
            compose = app.query_one("#compose", Input)
            detail = app.query_one("#detail", Static)

            assert [str(item.id) for item in members.children] == [
                f"member-{name}" for name in MEMBERS
            ]
            assert members.outer_size.width == 18  # 含右侧分隔边框
            assert timeline.size.width > 40
            assert compose.outer_size.height == 3  # 含顶部分隔边框
            # 详情栏默认折叠
            assert not detail.display

    run_async(scenario)


def test_detail_opens_on_selection_and_closes_on_escape(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            assert not app.detail_visible
            app.query_one("#members", ListView).focus()
            await pilot.press("down")
            await pilot.pause()
            assert app.selected_member in MEMBERS
            assert app.detail_visible
            assert app.query_one("#detail", Static).outer_size.width == 34

            await pilot.press("escape")
            await pilot.pause()
            assert app.selected_member is None
            assert not app.detail_visible

    run_async(scenario)


def test_detail_gives_way_when_the_terminal_is_narrow(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(DETAIL_MIN_WIDTH, 30)) as pilot:
            app.select_member("claude")
            await pilot.pause()
            assert app.detail_visible

            # 窄到 80 列:选中状态还在,但详情栏让位给时间线
            await pilot.resize_terminal(*MIN_SIZE)
            await pilot.pause()
            assert app.selected_member == "claude"
            assert not app.detail_visible
            assert app.query_one("#timeline", RichLog).size.width > 50

            # 拉宽回来,详情栏自己回来
            await pilot.resize_terminal(DETAIL_MIN_WIDTH, 30)
            await pilot.pause()
            assert app.detail_visible

    run_async(scenario)


def test_minimum_size_keeps_every_region_usable(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=MIN_SIZE):
            members = app.query_one("#members", ListView)
            timeline = app.query_one("#timeline", RichLog)
            compose = app.query_one("#compose", Input)

            assert members.outer_size.width == 18
            assert members.size.height > 10
            assert timeline.size.width >= 80 - 18 - 2  # 成员栏之外的都归时间线(减左右内边距)
            assert timeline.size.height >= 15
            assert compose.outer_size.width == timeline.outer_size.width  # 输入框与时间线同宽

    run_async(scenario)
