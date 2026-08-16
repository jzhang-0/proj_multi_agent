"""CON-002:会话列表 + 一块主画面、群聊/成员切换、80×24 可用。

画面本身另外看图确认(证据在 Goal 里),这里钉住尺寸与显隐规则。
"""

import asyncio

import pytest
from textual.widgets import Input, ListView, RichLog, Static

from bus import DeliveryOutcome, DeliveryResult, Message
from bus.paths import BusPaths
from console.app import MIN_SIZE, TIMELINE_ITEM_ID, ConsoleApp
from console.widgets import ConversationCard

MEMBERS = ("claude", "codex", "cursor", "agy")


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_app(paths):
    return ConsoleApp(paths, deliver=lambda message: True, members=MEMBERS)


def run_async(coro_factory):
    return asyncio.run(coro_factory())


def traffic(paths):
    message = Message.create("claude", "看一下", sender="human")
    return DeliveryResult(
        paths.queue / f"{message.id}.json", DeliveryOutcome.DELIVERED, message
    )


def test_conversation_list_then_one_stage_then_the_input(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)):
            conversations = app.query_one("#members", ListView)
            timeline = app.query_one("#timeline", RichLog)
            compose = app.query_one("#compose", Input)
            detail = app.query_one("#detail", Static)

            # 群聊排第一,后面才是成员
            assert [str(item.id) for item in conversations.children] == [
                TIMELINE_ITEM_ID,
                *(f"member-{name}" for name in MEMBERS),
            ]
            # 会话列表只占窄窄一条,宽度全给主画面
            assert conversations.outer_size.width == 22
            assert timeline.size.width > 90
            # 一起来停在群聊上:主画面是时间线,不是成员画面
            assert app.selected_member is None
            assert timeline.display and not detail.display
            # 输入框在底部通栏
            assert compose.outer_size.width == 120
            assert compose.outer_size.height == 3

    run_async(scenario)


def test_picking_a_member_swaps_the_stage_and_escape_comes_back(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            timeline = app.query_one("#timeline", RichLog)
            detail = app.query_one("#detail", Static)

            await pilot.press("down")  # 群聊下面第一个成员
            await pilot.pause()
            assert app.selected_member == MEMBERS[0]
            assert detail.display and not timeline.display

            await pilot.press("escape")
            await pilot.pause()
            assert app.selected_member is None
            assert timeline.display and not detail.display
            # Esc 回群聊时左栏高亮也回到第一项
            assert app.query_one("#members", ListView).index == 0

    run_async(scenario)


def test_group_card_counts_traffic_missed_while_away(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            card = app.query_one(ConversationCard)

            app.show_result(traffic(paths))
            await pilot.pause()
            assert app.unseen_traffic == 0  # 正在看群聊,不用记未读

            app.select_member("codex")
            await pilot.pause()
            app.show_result(traffic(paths))
            await pilot.pause()
            assert app.unseen_traffic == 1
            assert "未读 1 条" in str(card.render())

            app.select_member(None)
            await pilot.pause()
            assert app.unseen_traffic == 0
            assert "未读" not in str(card.render())

    run_async(scenario)


def test_minimum_size_keeps_every_region_usable(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=MIN_SIZE) as pilot:
            conversations = app.query_one("#members", ListView)
            timeline = app.query_one("#timeline", RichLog)
            compose = app.query_one("#compose", Input)

            assert conversations.outer_size.width == 22
            assert conversations.size.height > 10
            assert timeline.size.width >= MIN_SIZE[0] - 22 - 3  # 减分隔线与内边距
            assert timeline.size.height >= 15
            assert compose.outer_size.width == MIN_SIZE[0]

            # 80 列下选中成员,主画面照样整块给它
            await pilot.press("down")
            await pilot.pause()
            assert app.query_one("#detail", Static).size.width >= MIN_SIZE[0] - 22 - 3

    run_async(scenario)
