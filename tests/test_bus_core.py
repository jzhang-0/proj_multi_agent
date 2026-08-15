"""BUS-001:schema 校验、路径注入、死信不炸循环。

测试全部在临时目录里跑,不碰仓库根 `bus/`。
"""

import json

import pytest

from bus import (
    BusPaths,
    DeliveryOutcome,
    Hub,
    MalformedMessage,
    Message,
    deposit,
    pending,
    read_message,
)
from bus.paths import ENV_BUS_ROOT


def make_paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


# --- schema v1 ---------------------------------------------------------------


def test_four_required_fields_roundtrip():
    message = Message.create("codex", "跑一下测试", sender="claude")
    payload = message.to_dict()
    assert payload["to"] == "codex"
    assert payload["from"] == "claude"
    assert payload["text"] == "跑一下测试"
    assert payload["ts"]
    assert Message.from_dict(payload) == message


def test_optional_fields_are_parsed():
    payload = {
        "to": "claude",
        "from": "codex",
        "text": "收到",
        "ts": "2026-08-16 06:00:00",
        "id": "abc",
        "kind": "reply",
        "replyTo": "xyz",
    }
    message = Message.from_dict(payload)
    assert (message.id, message.kind, message.reply_to) == ("abc", "reply", "xyz")
    assert message.to_dict() == payload


def test_unknown_fields_are_preserved():
    payload = {
        "to": "claude",
        "from": "codex",
        "text": "hi",
        "ts": "2026-08-16 06:00:00",
        "future": {"x": 1},
    }
    message = Message.from_dict(payload)
    assert message.extra == {"future": {"x": 1}}
    assert message.to_dict()["future"] == {"x": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"from": "codex", "text": "hi", "ts": "t"},  # 缺 to
        {"to": "claude", "text": "hi", "ts": "t"},  # 缺 from
        {"to": "claude", "from": "codex", "ts": "t"},  # 缺 text
        {"to": "claude", "from": "codex", "text": "hi"},  # 缺 ts
        {"to": "", "from": "codex", "text": "hi", "ts": "t"},  # to 为空
        {"to": 1, "from": "codex", "text": "hi", "ts": "t"},  # to 类型错
        {"to": "a", "from": "b", "text": "hi", "ts": "t", "kind": 3},  # 可选字段类型错
        ["not", "an", "object"],
    ],
)
def test_malformed_payloads_rejected(payload):
    with pytest.raises(MalformedMessage):
        Message.from_dict(payload)


# --- 路径注入 -----------------------------------------------------------------


def test_paths_injection_precedence(tmp_path, monkeypatch):
    explicit = BusPaths.resolve(tmp_path / "explicit")
    assert explicit.queue == (tmp_path / "explicit" / "queue").resolve()

    monkeypatch.setenv(ENV_BUS_ROOT, str(tmp_path / "fromenv"))
    assert BusPaths.resolve().root == (tmp_path / "fromenv").resolve()

    monkeypatch.delenv(ENV_BUS_ROOT)
    assert BusPaths.resolve().root.name == "bus"


def test_deposit_and_read(tmp_path):
    paths = make_paths(tmp_path)
    path = deposit(Message.create("codex", "你好", sender="claude"), paths)
    assert pending(paths) == [path]
    assert read_message(path).text == "你好"


# --- 投递循环 -----------------------------------------------------------------


def test_malformed_message_goes_to_dead_letter_and_loop_continues(tmp_path):
    paths = make_paths(tmp_path)
    (paths.queue / "0-broken.json").write_text("{ 这不是 json", encoding="utf-8")
    (paths.queue / "1-nofields.json").write_text(json.dumps({"to": "codex"}), encoding="utf-8")
    good = deposit(Message.create("codex", "正常消息", sender="claude"), paths)

    delivered = []

    def collect(message):
        delivered.append(message)
        return True

    hub = Hub(paths, deliver=collect)
    outcomes = [r.outcome for r in hub.drain_once()]

    assert outcomes == [
        DeliveryOutcome.MALFORMED,
        DeliveryOutcome.MALFORMED,
        DeliveryOutcome.DELIVERED,
    ]
    assert [m.text for m in delivered] == ["正常消息"]
    assert pending(paths) == []
    assert sorted(p.name for p in paths.dead.glob("*.json")) == ["0-broken.json", "1-nofields.json"]
    assert (paths.dead / "0-broken.json.err").read_text(encoding="utf-8").strip()
    assert [p.name for p in paths.processed.glob("*.json")] == [good.name]


def test_deliver_exception_does_not_break_loop(tmp_path):
    paths = make_paths(tmp_path)
    deposit(Message.create("codex", "会炸的一条", sender="claude"), paths)
    deposit(Message.create("cursor", "后面这条要照常处理", sender="claude"), paths)

    seen = []

    def flaky(message):
        if message.to == "codex":
            raise RuntimeError("tmux 挂了")
        seen.append(message.to)
        return True

    results = Hub(paths, deliver=flaky).drain_once()
    assert [r.outcome for r in results] == [DeliveryOutcome.FAILED, DeliveryOutcome.DELIVERED]
    assert "tmux 挂了" in results[0].detail
    assert seen == ["cursor"]
    assert pending(paths) == []


def test_human_message_is_shown_not_delivered(tmp_path):
    paths = make_paths(tmp_path)
    deposit(Message.create("human", "汇报一下", sender="claude"), paths)

    def never(message):
        raise AssertionError("发给 human 的消息不应该投递")

    assert [r.outcome for r in Hub(paths, deliver=never).drain_once()] == [DeliveryOutcome.SHOWN]
