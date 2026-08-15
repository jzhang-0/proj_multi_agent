"""BUS-004:积压熔断与 32KB 正文上限。"""

from bus import BusPaths, DeliveryOutcome, Hub, Message, deposit, pending, read_message
from bus.policy import BACKLOG_CAP, MAX_TEXT_BYTES, OutboundPolicy


def message(text: str = "正常消息", *, to: str = "cursor", sender: str = "claude") -> Message:
    return Message.create(to, text, sender=sender)


def test_text_at_32kb_is_allowed_and_one_more_byte_is_rejected():
    policy = OutboundPolicy()

    assert policy.check(message("a" * MAX_TEXT_BYTES)).ok
    verdict = policy.check(message("a" * (MAX_TEXT_BYTES + 1)))

    assert not verdict.ok
    assert "发摘要和路径,不要发内容" in verdict.reason


def test_text_limit_counts_utf8_bytes_not_characters():
    policy = OutboundPolicy()
    chinese_character = "你"
    within_limit = chinese_character * (MAX_TEXT_BYTES // len(chinese_character.encode("utf-8")))

    assert policy.check(message(within_limit)).ok
    assert not policy.check(message(within_limit + chinese_character)).ok


def test_fiftieth_queued_message_is_allowed_and_next_is_rejected():
    policy = OutboundPolicy()

    assert policy.check(message(), unread_backlog=BACKLOG_CAP - 1).ok
    verdict = policy.check(message(), unread_backlog=BACKLOG_CAP)

    assert not verdict.ok
    assert "对方积压中" in verdict.reason


def test_hub_rejects_new_messages_after_recipient_reaches_backlog_cap(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    messages = [message(f"积压 {index}") for index in range(BACKLOG_CAP + 1)]
    for queued in messages:
        deposit(queued, paths)

    delivered = []

    def collect(queued):
        delivered.append(queued)
        return True

    policy = OutboundPolicy(rate_limit=BACKLOG_CAP + 1)
    results = Hub(paths, deliver=collect, policy=policy).drain_once()

    assert [result.outcome for result in results] == [
        *[DeliveryOutcome.DELIVERED] * BACKLOG_CAP,
        DeliveryOutcome.REJECTED,
    ]
    assert delivered == messages[:BACKLOG_CAP]

    receipts = [read_message(path) for path in pending(paths)]
    assert len(receipts) == 1
    assert receipts[0].sender == "bus"
    assert receipts[0].to == "claude"
    assert receipts[0].reply_to == messages[-1].id
    assert "对方积压中" in receipts[0].text


def test_backlog_is_counted_per_recipient(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    for index in range(BACKLOG_CAP):
        deposit(message(f"cursor {index}"), paths)
    other_recipient = message("第一条", to="codex")
    deposit(other_recipient, paths)

    delivered = []

    def collect(queued):
        delivered.append(queued)
        return True

    policy = OutboundPolicy(rate_limit=BACKLOG_CAP + 1)
    results = Hub(paths, deliver=collect, policy=policy).drain_once()

    assert results[-1].outcome == DeliveryOutcome.DELIVERED
    assert delivered[-1] == other_recipient


def test_hub_rejects_oversize_message_and_queues_actionable_receipt(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    oversized = message("x" * (MAX_TEXT_BYTES + 1))
    deposit(oversized, paths)

    def never_deliver(queued):
        raise AssertionError(f"超长消息不应投递:{queued.id}")

    results = Hub(paths, deliver=never_deliver).drain_once()

    assert [result.outcome for result in results] == [DeliveryOutcome.REJECTED]
    receipt = read_message(pending(paths)[0])
    assert receipt.to == oversized.sender
    assert receipt.reply_to == oversized.id
    assert "发摘要和路径,不要发内容" in receipt.text
