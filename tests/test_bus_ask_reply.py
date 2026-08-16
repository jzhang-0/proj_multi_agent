"""BUS-007:msg 普通兼容、ask 阻塞等待与 reply 关联。"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

import bus.cli
from bus import BusPaths, Message, deposit, pending, read_message
from bus.ask import (
    DEFAULT_ASK_TIMEOUT_SECONDS,
    AskError,
    load_reply,
    store_ask,
    store_reply,
    wait_for_reply,
)
from bus.cli import main
from bus.sanitize import format_for_injection

ROOT = Path(__file__).resolve().parents[1]


def paths_for(tmp_path: Path) -> BusPaths:
    return BusPaths.resolve(tmp_path / "bus").ensure()


def ask_message(*, ask_id: str = "ask-123") -> Message:
    return Message.create(
        "claude",
        "请确认测试结果",
        sender="human",
        kind="ask",
        message_id=ask_id,
    )


def test_root_msg_keeps_ordinary_cli_and_four_field_json(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    environment = os.environ.copy()
    environment.update({"AGENT_NAME": "codex"})

    result = subprocess.run(
        [str(ROOT / "msg"), "agy", "a", "b", "c", "--bus-root", str(paths.root)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "[msg] codex -> agy: 已进入队列"
    payload = json.loads(pending(paths)[0].read_text(encoding="utf-8"))
    assert set(payload) == {"to", "from", "text", "ts"}
    assert payload | {"ts": "ignored"} == {
        "to": "agy",
        "from": "codex",
        "text": "a b c",
        "ts": "ignored",
    }


def test_missing_message_text_keeps_v0_usage_and_exit_code() -> None:
    output = io.StringIO()

    result = main(["codex"], stdout=output)

    assert result == 1
    assert "用法: msg <收件人> <内容...>" in output.getvalue()


def test_ask_uses_default_ten_minute_timeout_and_prints_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    seen = {}

    def answer(ask_id, actual_paths, *, timeout):
        seen.update({"ask_id": ask_id, "paths": actual_paths, "timeout": timeout})
        return Message.create(
            "human",
            "测试已通过",
            sender="claude",
            kind="reply",
            reply_to=ask_id,
        )

    monkeypatch.setattr(bus.cli, "wait_for_reply", answer)
    output = io.StringIO()

    result = main(
        ["--ask", "claude", "测试", "通过了吗"],
        paths=paths,
        sender="human",
        stdout=output,
    )

    assert result == 0
    assert seen["timeout"] == DEFAULT_ASK_TIMEOUT_SECONDS
    queued = read_message(pending(paths)[0])
    assert queued.kind == "ask"
    assert queued.id == seen["ask_id"]
    assert queued.text == "测试 通过了吗"
    assert (paths.asks / f"{queued.id}.json").is_file()
    assert "[reply] claude: 测试已通过" in output.getvalue()


def test_reply_finds_original_asker_and_persists_association(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    ask = ask_message()
    store_ask(ask, paths)
    output = io.StringIO()

    result = main(
        ["--reply", ask.id or "", "结果", "在日志里"],
        paths=paths,
        sender="claude",
        stdout=output,
    )

    assert result == 0
    reply = read_message(pending(paths)[0])
    assert (reply.to, reply.sender, reply.kind, reply.reply_to) == (
        "human",
        "claude",
        "reply",
        ask.id,
    )
    assert reply.text == "结果 在日志里"
    assert load_reply(ask.id or "", paths) == reply
    assert "已回复 ask ask-123" in output.getvalue()


def test_only_original_recipient_can_reply(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    store_ask(ask_message(), paths)
    errors = io.StringIO()

    result = main(
        ["--reply", "ask-123", "冒充回复"],
        paths=paths,
        sender="cursor",
        stderr=errors,
    )

    assert result == 2
    assert "只有提问收件人 claude" in errors.getvalue()
    assert pending(paths) == []


def test_waiter_returns_correlated_bus_rejection_without_waiting(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    ask = ask_message()
    store_ask(ask, paths)
    receipt = Message.create(
        ask.sender,
        "[总线] 问题未送达",
        sender="bus",
        kind="receipt",
        reply_to=ask.id,
    )
    deposit(receipt, paths)

    assert wait_for_reply(ask.id or "", paths, timeout=0) == receipt


def test_waiter_times_out_without_sleep_when_timeout_is_zero(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    ask = ask_message()
    store_ask(ask, paths)

    assert wait_for_reply(ask.id or "", paths, timeout=0) is None


def test_waiter_polls_until_reply_arrives(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    ask = ask_message()
    store_ask(ask, paths)
    now = [0.0]
    reply = Message.create(
        ask.sender,
        "稍后到达的回复",
        sender=ask.to,
        kind="reply",
        reply_to=ask.id,
    )

    def advance(seconds: float) -> None:
        now[0] += seconds
        store_reply(reply, paths)

    result = wait_for_reply(
        ask.id or "",
        paths,
        timeout=1,
        poll_interval=0.1,
        clock=lambda: now[0],
        sleep=advance,
    )

    assert result == reply
    assert now[0] == 0.1


def test_ask_delivery_contains_id_and_reply_instructions() -> None:
    line = format_for_injection(ask_message())

    assert "ask ask-123" in line
    assert 'amux msg --reply ask-123 "你的答复"' in line


def test_reply_rejects_path_traversal_id(tmp_path: Path) -> None:
    with pytest.raises(AskError, match="ask id"):
        wait_for_reply("../outside", paths_for(tmp_path), timeout=0)
