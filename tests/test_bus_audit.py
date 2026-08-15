"""BUS-008:审计日志 schema、预览截断 + 全文另存、10MB 轮转。"""

import json

import pytest

from bus import DeliveryOutcome, Hub, Message, deposit
from bus.audit import PREVIEW_CHARS, AuditEvent, AuditLog
from bus.paths import BusPaths


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def events_in(log):
    return [entry["event"] for entry in log.entries()]


def test_entry_has_fixed_schema(paths):
    log = AuditLog(paths)
    message = Message.create("codex", "跑一下测试", sender="claude", kind="task")
    entry = log.record(AuditEvent.DELIVER, message)

    assert entry["event"] == "deliver"
    assert (entry["from"], entry["to"]) == ("claude", "codex")
    assert entry["id"] == message.id
    assert entry["preview"] == "跑一下测试"
    assert entry["kind"] == "task"
    assert "reason" not in entry
    assert json.loads(paths.log.read_text(encoding="utf-8").strip()) == entry


def test_rejection_records_reason(paths):
    log = AuditLog(paths)
    message = Message.create("codex", "复读", sender="claude")
    entry = log.record(AuditEvent.REJECTED, message, "复读")
    assert entry["event"] == "rejected"
    assert entry["reason"] == "复读"


def test_long_body_is_previewed_and_stored_separately(paths):
    log = AuditLog(paths)
    body = "很长的正文" * 100
    message = Message.create("codex", body, sender="claude")
    entry = log.record(AuditEvent.DEPOSIT, message)

    assert len(entry["preview"]) == PREVIEW_CHARS + 1  # 80 字符 + 省略号
    assert entry["preview"].endswith("…")
    assert entry["body"] == f"bodies/{message.id}.txt"
    assert log.read_body(entry) == body


def test_preview_is_sanitized_but_full_text_keeps_evidence(paths):
    log = AuditLog(paths)
    message = Message.create("codex", "\x1b]0;假标题\x07正常内容", sender="claude")
    entry = log.record(AuditEvent.DEPOSIT, message)

    assert entry["preview"] == "正常内容"
    assert "\x1b" not in json.dumps(entry)
    assert log.read_body(entry) == message.text  # 取证用,原样留着


def test_log_rotates_past_the_size_cap(paths):
    log = AuditLog(paths, max_bytes=400)
    for index in range(12):
        log.record(AuditEvent.DEPOSIT, Message.create("codex", f"第 {index} 条", sender="claude"))

    rotated = sorted(paths.root.glob("log-*.jsonl"))
    assert rotated, "超过上限应该轮转出历史文件"
    # 轮转在写之前判定,所以当前文件最多超出上限一条;关键是它没有一路长下去
    assert len(log.entries()) < 12
    total = len(log.entries()) + sum(
        len(path.read_text(encoding="utf-8").strip().splitlines()) for path in rotated
    )
    assert total == 12


def test_entries_skips_broken_lines(paths):
    log = AuditLog(paths)
    log.record(AuditEvent.DEPOSIT, Message.create("codex", "好的一行", sender="claude"))
    with paths.log.open("a", encoding="utf-8") as handle:
        handle.write("{ 这行坏了\n")
    assert [entry["preview"] for entry in log.entries()] == ["好的一行"]


def test_hub_records_deposit_deliver_failed_and_rejected(paths):
    log = AuditLog(paths)
    hub = Hub(paths, deliver=lambda message: message.to != "cursor", audit=log)

    deposit(Message.create("codex", "正常一条", sender="claude"), paths)
    deposit(Message.create("cursor", "投不出去的一条", sender="claude"), paths)
    deposit(Message.create("codex", "复读一条", sender="claude"), paths)
    deposit(Message.create("codex", "复读一条", sender="claude"), paths)
    (paths.queue / "9999-broken.json").write_text("{ 不是 json", encoding="utf-8")

    outcomes = [result.outcome for result in hub.drain_once()]
    assert outcomes == [
        DeliveryOutcome.DELIVERED,
        DeliveryOutcome.FAILED,
        DeliveryOutcome.DELIVERED,
        DeliveryOutcome.REJECTED,
        DeliveryOutcome.MALFORMED,
    ]

    # 入队 4 条 → 4 个 deposit;投递结果各自一条;拒收那条还会产生一条回执的 deposit
    recorded = events_in(log)
    assert recorded.count(AuditEvent.DEPOSIT) == 5
    assert recorded.count(AuditEvent.DELIVER) == 2
    assert recorded.count(AuditEvent.DELIVER_FAILED) == 1
    assert recorded.count(AuditEvent.REJECTED) == 1
    assert recorded.count(AuditEvent.MALFORMED) == 1

    rejected = next(entry for entry in log.entries() if entry["event"] == "rejected")
    assert "一模一样" in rejected["reason"]
