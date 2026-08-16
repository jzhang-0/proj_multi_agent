"""CON-004:回车即发、@ 补全、默认收件人、发言历史。"""

import asyncio

import pytest
from textual.widgets import Static

from bus.paths import BusPaths
from console.app import ConsoleApp
from console.compose import ComposeInput, completion_prefix, matching_members, split_address

MEMBERS = ("claude", "codex", "cursor", "agy")


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_app(paths, sink=None):
    """投递函数当探针:内嵌的投递循环会立刻把消息投出去并清空队列,
    所以断言看的是"投出去了什么",不是"队列里还剩什么"。"""

    def deliver(message):
        if sink is not None:
            sink.append(message)
        return True

    return ConsoleApp(paths, deliver=deliver, members=MEMBERS)


def run_async(factory):
    return asyncio.run(factory())


async def wait_for(pilot, predicate, rounds=200):
    for _ in range(rounds):
        if predicate():
            return True
        await pilot.pause(0.02)
    return False


# --- 纯函数 -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@codex 跑一下测试", ("codex", "跑一下测试")),
        ("  @claude   带空格的正文  ", ("claude", "带空格的正文")),
        ("没有前缀的一句话", (None, "没有前缀的一句话")),
        ("邮箱 a@b 不算前缀", (None, "邮箱 a@b 不算前缀")),
    ],
)
def test_split_address(raw, expected):
    assert split_address(raw) == expected


def test_completion_prefix_only_triggers_on_the_at_token():
    assert completion_prefix("@cl", 3) == "cl"
    assert completion_prefix("@", 1) == ""
    assert completion_prefix("@codex 已经写完了", 15) is None
    assert matching_members("c", MEMBERS) == ("claude", "codex", "cursor")


# --- 交互 ---------------------------------------------------------------


def test_enter_sends_and_bus_gets_the_message(paths):
    sent = []
    app = make_app(paths, sent)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            await pilot.pause()
            compose.value = "@codex 跑一下测试"
            await pilot.press("enter")
            await pilot.pause()

            assert await wait_for(pilot, lambda: len(sent) == 1)
            assert [(m.sender, m.to, m.text) for m in sent] == [("human", "codex", "跑一下测试")]
            assert compose.value == ""

    run_async(scenario)


def test_at_triggers_completion_and_tab_accepts(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            await pilot.press("@", "c")
            await pilot.pause()
            assert compose.candidates == ("claude", "codex", "cursor")
            assert app.query_one("#suggestions", Static).display

            await pilot.press("down")  # 方向键选下一个
            await pilot.pause()
            assert compose.current_candidate == "codex"

            await pilot.press("tab")  # Tab 落定
            await pilot.pause()
            assert compose.value == "@codex "
            assert compose.candidates == ()
            assert not app.query_one("#suggestions", Static).display

    run_async(scenario)


def test_message_without_at_goes_to_the_last_target(paths):
    sent = []
    app = make_app(paths, sent)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "@cursor 第一句"
            await pilot.press("enter")
            await pilot.pause()
            assert app.last_target == "cursor"
            assert "回车发给 cursor" in compose.placeholder

            compose.value = "第二句不写前缀"
            await pilot.press("enter")
            await pilot.pause()

            assert await wait_for(pilot, lambda: len(sent) == 2)
            assert [(m.to, m.text) for m in sent] == [
                ("cursor", "第一句"),
                ("cursor", "第二句不写前缀"),
            ]

    run_async(scenario)


def test_inside_a_member_conversation_plain_text_is_typed_into_its_terminal(paths):
    """选中成员时不带 @ 的一行直接键入它的终端;`@名字` 仍然走群聊总线。"""

    class RecordingController:
        def __init__(self):
            self.typed = []

        def type_text(self, target, text):
            from console.control import ControlFeedback

            self.typed.append((target, text))
            return ControlFeedback("type", target, True, text)

    sent = []
    controller = RecordingController()
    app = ConsoleApp(
        paths,
        deliver=lambda message: sent.append(message) or True,
        members=MEMBERS,
        controller=controller,
    )

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            app.select_member("codex")
            await pilot.pause()
            assert "直连 codex 的终端" in compose.placeholder

            compose.focus()
            compose.value = "继续做 GATE-004"
            await pilot.press("enter")
            # 注入在工作线程里跑(文本和 Enter 之间要留一口气)
            assert await wait_for(pilot, lambda: controller.typed)
            assert controller.typed == [("codex", "继续做 GATE-004")]
            assert sent == []  # 没上总线
            assert compose.value == ""

            # 想发群聊就写 @:直连不吃这一条
            compose.value = "@cursor 这条走群聊"
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: len(sent) == 1)
            assert (sent[0].to, sent[0].text) == ("cursor", "这条走群聊")
            assert controller.typed == [("codex", "继续做 GATE-004")]

            # 回群聊会话后,不带 @ 的一行又回到"发给上一个对话对象"
            app.select_member(None)
            await pilot.pause()
            compose.value = "回群聊说一句"
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: len(sent) == 2)
            assert (sent[1].to, sent[1].text) == ("cursor", "回群聊说一句")

    run_async(scenario)


def test_without_any_target_it_says_so_instead_of_guessing(paths):
    sent = []
    app = make_app(paths, sent)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "还没指定收件人"
            await pilot.press("enter")
            await pilot.pause()
            assert sent == []
            from console.widgets import Timeline

            log = app.query_one("#timeline", Timeline)
            lines = ["".join(segment.text for segment in line) for line in log.lines]
            assert any("先用 @名字 指定收件人" in line for line in lines)

    run_async(scenario)


def test_up_and_down_walk_the_send_history(paths):
    app = make_app(paths)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            for text in ("@codex 第一条", "@codex 第二条"):
                compose.value = text
                await pilot.press("enter")
                await pilot.pause()

            await pilot.press("up")
            await pilot.pause()
            assert compose.value == "@codex 第二条"

            await pilot.press("up")
            await pilot.pause()
            assert compose.value == "@codex 第一条"

            await pilot.press("down")
            await pilot.pause()
            assert compose.value == "@codex 第二条"

            await pilot.press("down")
            await pilot.pause()
            assert compose.value == ""  # 翻回最新之后是空行

    run_async(scenario)
