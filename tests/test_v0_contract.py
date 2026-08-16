"""BUS-009:v0 契约回归。

这些用法是成员和人已经在用的公共契约(架构决策 §3),实现怎么演化都
不许变:`./msg <收件人> <内容...>`、四字段 JSON、`human` 保留名。测试
一律用临时 bus 根目录,不碰仓库根 `bus/`。
"""

import json
import subprocess
import sys

import pytest

from bus import DeliveryOutcome, Hub, Message, pending, read_message
from bus.message import REQUIRED_FIELDS
from bus.paths import BusPaths, repo_root

REPO_ROOT = repo_root()
MSG = REPO_ROOT / "msg"
HUB = REPO_ROOT / "hub.py"


def run_entry(script, args, bus_root, env_extra=None):
    """用系统 python3 跑入口脚本——成员敲的就是这个,必须能自己切环境。"""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(bus_root)}
    env.update(env_extra or {})
    return subprocess.run(
        ["python3", str(script), *args, "--bus-root", str(bus_root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )


@pytest.fixture
def bus_root(tmp_path):
    return tmp_path / "bus"


def only_message(bus_root):
    paths = BusPaths.resolve(bus_root)
    queued = pending(paths)
    assert len(queued) == 1, f"队列里应该正好一条,实际 {len(queued)}"
    return json.loads(queued[0].read_text(encoding="utf-8"))


def test_msg_three_arg_usage_still_works(bus_root):
    result = run_entry(MSG, ["codex", "帮我跑一下测试"], bus_root)
    assert result.returncode == 0, result.stderr
    assert "已进入队列" in result.stdout

    payload = only_message(bus_root)
    assert payload["to"] == "codex"
    assert payload["from"] == "human"  # 没设 AGENT_NAME 就是人
    assert payload["text"] == "帮我跑一下测试"


def test_msg_joins_multiple_words_and_takes_agent_name(bus_root):
    args = ["cursor", "写", "一个", "fizzbuzz"]
    result = run_entry(MSG, args, bus_root, {"AGENT_NAME": "claude"})
    assert result.returncode == 0, result.stderr

    payload = only_message(bus_root)
    assert payload["from"] == "claude"
    assert payload["text"] == "写 一个 fizzbuzz"


def test_queued_json_keeps_the_four_frozen_fields(bus_root):
    run_entry(MSG, ["codex", "四字段还在吗"], bus_root)
    payload = only_message(bus_root)
    assert set(REQUIRED_FIELDS) <= set(payload)
    assert all(isinstance(payload[field], str) for field in REQUIRED_FIELDS)
    # 时间戳仍然是 v0 的 "YYYY-MM-DD HH:MM:SS" 形状
    assert len(payload["ts"]) == 19 and payload["ts"][4] == "-" and payload["ts"][13] == ":"


def test_msg_without_enough_args_prints_usage_and_fails(bus_root):
    result = run_entry(MSG, ["codex"], bus_root)
    assert result.returncode == 1
    assert "用法: msg <收件人> <内容...>" in result.stdout


def test_hub_entry_drains_the_queue_and_shows_human_messages(bus_root):
    run_entry(MSG, ["human", "向人类报到"], bus_root, {"AGENT_NAME": "claude"})
    result = run_entry(HUB, ["--once"], bus_root)

    assert result.returncode == 0, result.stderr
    assert "claude -> human: 向人类报到" in result.stdout
    assert "★" in result.stdout  # v0 里发给 human 的标记
    assert pending(BusPaths.resolve(bus_root)) == []


def test_human_is_never_injected_into_a_terminal(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    from bus.queue import deposit

    deposit(Message.create("human", "只上屏,不投递", sender="claude"), paths)

    def must_not_be_called(message):
        raise AssertionError("发给 human 的消息不许进 tmux")

    results = Hub(paths, deliver=must_not_be_called).drain_once()
    assert [result.outcome for result in results] == [DeliveryOutcome.SHOWN]


def test_message_written_by_v0_shape_is_still_readable(tmp_path):
    """外部程序(或旧版本 msg)直接写的四字段文件必须照样能投。"""
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    raw = {"to": "codex", "from": "claude", "text": "老格式的一条", "ts": "2026-08-16 07:20:00"}
    (paths.queue / "1755300000000000000-abcdef.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    message = read_message(pending(paths)[0])
    assert (message.to, message.sender, message.text, message.ts) == tuple(raw.values())
    assert message.id is None  # 可选字段缺席不算畸形


@pytest.mark.skipif(sys.platform == "win32", reason="入口是 POSIX shebang 脚本")
def test_entries_are_thin(bus_root):
    """薄入口:两个 v0 入口都不再自己实现投递逻辑。"""
    for script in (MSG, HUB):
        source = script.read_text(encoding="utf-8")
        assert "send-keys" not in source
        assert "json.dump" not in source
        assert len(source.splitlines()) < 40
