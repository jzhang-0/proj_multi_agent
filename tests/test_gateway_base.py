"""GATE-001:网关抽象、单 bot 路由与代发署名、与具体 IM 解耦。"""

import asyncio

import pytest

from bus import Message, deposit, pending, read_message
from bus.audit import AuditEvent, AuditLog
from bus.hub import Hub, is_screen_only
from bus.paths import BusPaths
from gateway import Gateway, GroupMessage, GroupPost, display_name, sender_name
from gateway.router import route_group_message

MEMBERS = ("claude", "codex", "cursor", "agy")


class FakeAdapter:
    """假 IM:只把要发进群的消息记下来。证明网关不认识任何具体平台。"""

    def __init__(self):
        self.posts: list[GroupPost] = []
        self.on_message = None
        self.started = False
        self.stopped = False

    async def start(self, on_message):
        self.on_message = on_message
        self.started = True

    async def stop(self):
        self.stopped = True

    async def post(self, post):
        self.posts.append(post)


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_gateway(paths, adapter=None):
    return Gateway(adapter or FakeAdapter(), paths, members=lambda: MEMBERS)


def queued(paths):
    return [read_message(path) for path in pending(paths)]


# --- 收群消息 → 入队 bus -------------------------------------------------


def test_at_routing_puts_the_message_on_the_bus(paths):
    gateway = make_gateway(paths)
    route = gateway.on_group_message(GroupMessage("小明", "@codex 跑一下测试"))

    assert route.error == ""
    sent = queued(paths)
    assert [(m.sender, m.to, m.text) for m in sent] == [("im:小明", "codex", "跑一下测试")]


def test_without_at_it_follows_the_last_target_in_that_room(paths):
    gateway = make_gateway(paths)
    gateway.on_group_message(GroupMessage("小明", "@cursor 第一句"))
    gateway.on_group_message(GroupMessage("小明", "第二句不写前缀"))

    assert [(m.to, m.text) for m in queued(paths)] == [
        ("cursor", "第一句"),
        ("cursor", "第二句不写前缀"),
    ]


def test_rooms_keep_separate_last_targets(paths):
    gateway = make_gateway(paths)
    gateway.on_group_message(GroupMessage("小明", "@cursor 甲群的话", room="甲"))
    route = gateway.on_group_message(GroupMessage("小红", "乙群没 @ 过人", room="乙"))
    assert "要先 @ 一个成员" in route.error


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", "消息是空的"),
        ("没有前缀", "要先 @ 一个成员"),
        ("@查无此人 干活", "没有成员叫 查无此人"),
    ],
)
def test_routing_errors_are_explained_not_swallowed(paths, text, expected):
    route = make_gateway(paths).on_group_message(GroupMessage("小明", text))
    assert expected in route.error
    assert queued(paths) == []  # 路由失败不入队


def test_remote_sender_is_marked_and_is_not_human():
    assert sender_name("小明") == "im:小明"
    assert sender_name("") == "im:anonymous"
    assert sender_name("小明") != "human"  # 所以照样受限频约束
    assert display_name("im:小明") == "小明(手机)"
    assert display_name("claude") == "claude"


def test_routing_sanitizes_terminal_escapes():
    route = route_group_message("小明", "@codex \x1b]0;假标题\x07正常内容", members=MEMBERS)
    assert route.message is not None
    assert route.message.text == "正常内容"


# --- 远程收件人由网关代投 -------------------------------------------------


def test_bus_does_not_inject_remote_recipients_into_tmux(paths):
    assert is_screen_only("im:小明")
    assert is_screen_only("human")
    assert not is_screen_only("codex")

    deposit(Message.create("im:小明", "成员回给手机上的人", sender="claude"), paths)

    def must_not_be_called(message):
        raise AssertionError("im: 开头的收件人没有 tmux 会话,不该注入")

    results = Hub(paths, deliver=must_not_be_called).drain_once()
    assert [str(result.outcome) for result in results] == ["shown"]


# --- 订阅 bus → 发回群 ---------------------------------------------------


def test_bus_traffic_is_forwarded_to_the_group_with_a_byline(paths):
    adapter = FakeAdapter()
    gateway = make_gateway(paths, adapter)
    audit = AuditLog(paths)
    audit.record(AuditEvent.DEPOSIT, Message.create("codex", "帮我 review", sender="claude"))
    audit.record(AuditEvent.REJECTED, Message.create("codex", "复读", sender="agy"), "10 秒内重复")
    audit.record(AuditEvent.DELIVER, Message.create("codex", "帮我 review", sender="claude"))

    asyncio.run(gateway.pump_once())

    # deliver 与 deposit 重复,不转;拒收要转并说明原因
    assert [(post.author, post.kind) for post in adapter.posts] == [
        ("claude", "message"),
        ("agy", "notice"),
    ]
    assert adapter.posts[0].rendered() == "claude: → codex: 帮我 review"
    assert "被拒收:10 秒内重复" in adapter.posts[1].rendered()


def test_startup_does_not_dump_history_into_the_group(paths):
    audit = AuditLog(paths)
    audit.record(AuditEvent.DEPOSIT, Message.create("codex", "启动前的老消息", sender="claude"))

    adapter = FakeAdapter()
    gateway = make_gateway(paths, adapter)
    gateway.catch_up()
    audit.record(AuditEvent.DEPOSIT, Message.create("codex", "启动后的新消息", sender="claude"))
    asyncio.run(gateway.pump_once())

    assert [post.text for post in adapter.posts] == ["→ codex: 启动后的新消息"]


def test_run_starts_and_stops_the_adapter_and_pumps(paths):
    adapter = FakeAdapter()
    gateway = make_gateway(paths, adapter)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(gateway.run(stop))
        await asyncio.sleep(0.05)
        assert adapter.started
        assert adapter.on_message is not None

        # adapter 收到群消息 → 网关入队 → 下一轮 pump 发回群
        adapter.on_message(GroupMessage("小明", "@claude 手机上派个活"))
        await asyncio.sleep(0.35)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        return adapter

    result = asyncio.run(scenario())
    assert result.stopped
    assert any("手机上派个活" in post.text for post in result.posts)
    assert [(m.sender, m.to) for m in queued(paths)] == [("im:小明", "claude")]


def test_routing_error_goes_back_to_the_group(paths):
    adapter = FakeAdapter()
    gateway = make_gateway(paths, adapter)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(gateway.run(stop))
        await asyncio.sleep(0.05)
        adapter.on_message(GroupMessage("小明", "谁都没 @"))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert any("要先 @ 一个成员" in post.text for post in adapter.posts)
    assert queued(paths) == []


def test_gateway_core_knows_nothing_about_any_im_platform():
    """与具体 IM 解耦:核心模块里不许出现平台名字或网络库。"""
    import pathlib

    import gateway

    root = pathlib.Path(gateway.__file__).parent
    for name in ("base.py", "router.py"):
        source = (root / name).read_text(encoding="utf-8").lower()
        for platform in ("discord", "telegram", "slack", "http", "socket", "requests"):
            assert platform not in source, f"{name} 里出现了平台/传输细节:{platform}"
