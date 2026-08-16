"""GATE-004:白名单、来源标记 + 清洗限频、危险指令本机二次确认。"""

import asyncio

import pytest

from bus import Message, pending, read_message
from bus.audit import AuditLog
from bus.paths import BusPaths
from bus.policy import OutboundPolicy
from gateway.base import Gateway, GroupMessage
from gateway.security import PendingStore, SecurityPolicy, danger_in

MEMBERS = ("claude", "codex")


class FakeAdapter:
    def __init__(self):
        self.posts = []
        self.on_message = None

    async def start(self, on_message):
        self.on_message = on_message

    async def stop(self):
        pass

    async def post(self, post):
        self.posts.append(post)


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def make_gateway(paths, *, users=("小明",), rooms=("default",), adapter=None):
    return Gateway(
        adapter or FakeAdapter(),
        paths,
        members=lambda: MEMBERS,
        security=SecurityPolicy(frozenset(rooms), frozenset(users)),
    )


def queued(paths):
    return [read_message(path) for path in pending(paths)]


# --- 白名单 -------------------------------------------------------------


def test_only_whitelisted_users_are_served(paths):
    gateway = make_gateway(paths, users=("小明",))
    assert gateway.on_group_message(GroupMessage("小明", "@claude 干活")).error == ""

    refused = gateway.on_group_message(GroupMessage("陌生人", "@claude 也想派活"))
    assert "不在白名单里" in refused.error
    assert [m.sender for m in queued(paths)] == ["im:小明"]


def test_only_whitelisted_rooms_are_served(paths):
    gateway = make_gateway(paths, rooms=("家里",))
    refused = gateway.on_group_message(GroupMessage("小明", "@claude 干活", room="别的群"))
    assert "不在白名单里" in refused.error
    assert queued(paths) == []


def test_empty_whitelist_refuses_everyone_and_says_how_to_fix(paths):
    gateway = make_gateway(paths, users=())
    refused = gateway.on_group_message(GroupMessage("小明", "@claude 干活"))
    assert "还没配白名单" in refused.error
    assert "gateway.toml" in refused.error
    assert queued(paths) == []


# --- 来源标记 + 清洗与限频 -----------------------------------------------


def test_remote_sender_is_marked_and_still_rate_limited(paths):
    gateway = make_gateway(paths)
    for index in range(9):
        gateway.on_group_message(GroupMessage("小明", f"@claude 第 {index} 条"))

    sent = queued(paths)
    assert {m.sender for m in sent} == {"im:小明"}  # 来源一眼可辨

    # 限频对它照常生效:第 9 条会被总线拒收(human 才有豁免)
    policy = OutboundPolicy()
    verdicts = []
    for message in sent:
        verdict = policy.check(message)
        verdicts.append(verdict.ok)
        if verdict.ok:
            policy.record(message)
    assert verdicts[-1] is False
    assert "发送过于频繁" in policy.check(sent[-1]).reason


def test_remote_text_is_sanitized_before_it_reaches_the_bus(paths):
    gateway = make_gateway(paths)
    gateway.on_group_message(GroupMessage("小明", "@claude \x1b]0;假标题\x07正常内容\x1b[2J"))
    assert [m.text for m in queued(paths)] == ["正常内容"]


# --- 危险指令降权 ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,label",
    [
        ("@claude git push 到远端", "推送代码"),
        ("@claude rm -rf build 目录", "删除文件"),
        ("@claude brew install cowsay", "安装软件"),
        ("@claude 看看 /etc/hosts", "访问仓库外路径"),
        ("@claude sudo 重启一下", "改权限或凭证"),
    ],
)
def test_dangerous_instructions_are_recognized(text, label):
    assert danger_in(text) == label


def test_ordinary_instructions_are_not_flagged():
    assert danger_in("@claude 跑一下 tests/test_bus_core.py") == ""
    assert danger_in("@codex 把结论汇报给 human") == ""


def test_dangerous_remote_instruction_waits_for_local_confirmation(paths):
    gateway = make_gateway(paths)
    route = gateway.on_group_message(GroupMessage("小明", "@claude git push 一下"))

    # 群里得到明确答复,但消息没有直接进队列给成员
    assert "本机确认" in route.error
    assert [(m.to, m.sender) for m in queued(paths)] == [("human", "bus")]
    assert "确认放行请在本机跑" in queued(paths)[0].text

    store = PendingStore(paths)
    items = store.entries()
    assert len(items) == 1
    assert items[0]["label"] == "推送代码"
    assert items[0]["user"] == "小明"


def test_local_approval_releases_the_instruction(paths):
    adapter = FakeAdapter()
    gateway = make_gateway(paths, adapter=adapter)
    gateway.on_group_message(GroupMessage("小明", "@claude git push 一下"))
    request_id = str(PendingStore(paths).entries()[0]["id"])

    assert PendingStore(paths).approve(request_id)  # 本机的人点头
    asyncio.run(gateway.pump_once())

    released = [m for m in queued(paths) if m.to == "claude"]
    assert [m.text for m in released] == ["git push 一下"]
    assert [m.sender for m in released] == ["im:小明"]  # 放行不改身份
    assert any("本机已确认" in post.text for post in adapter.posts)
    assert PendingStore(paths).entries() == []  # 取过就不再重复放行


def test_local_rejection_drops_it_for_good(paths):
    gateway = make_gateway(paths)
    gateway.on_group_message(GroupMessage("小明", "@claude rm -rf 整个目录"))
    store = PendingStore(paths)
    request_id = str(store.entries()[0]["id"])

    assert store.reject(request_id)
    asyncio.run(gateway.pump_once())
    assert [m.to for m in queued(paths)] == ["human"]  # 只剩当初那条叫人的提醒
    assert store.entries() == []


def test_remote_human_claim_does_not_bypass_confirmation(paths):
    """手机上的人自称 human 也没用:远程指令天然弱于本机指令。"""
    gateway = make_gateway(paths, users=("human",))
    route = gateway.on_group_message(GroupMessage("human", "@claude git push"))

    assert "本机确认" in route.error
    assert [m.to for m in queued(paths)] == ["human"]
    assert PendingStore(paths).entries()[0]["user"] == "human"


def test_confirmation_request_is_auditable(paths):
    gateway = make_gateway(paths)
    gateway.on_group_message(GroupMessage("小明", "@claude git push 一下"))
    events = [entry["event"] for entry in AuditLog(paths).entries()]
    assert "deposit" in events  # 叫人的那条也进审计日志,事后查得到


def test_pending_store_survives_a_restart(paths):
    """确认命令是另一个进程,状态必须在盘上。"""
    store = PendingStore(paths)
    message = Message.create("claude", "git push 一下", sender="im:小明")
    request_id = store.add(message, label="推送代码", user="小明", room="default")

    fresh = PendingStore(paths)  # 模拟另一个进程
    assert [item["id"] for item in fresh.entries()] == [request_id]
    assert fresh.approve(request_id)
    released = fresh.take_approved()
    assert [message.text for _, message in released] == ["git push 一下"]
