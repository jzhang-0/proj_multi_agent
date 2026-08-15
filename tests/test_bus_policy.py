"""BUS-002:传输层去重与回执。

假时钟推进窗口,不睡真时间;队列全在临时目录里。
"""

import pytest

from bus import DeliveryOutcome, Hub, Message, OutboundPolicy, deposit, pending, read_message
from bus.paths import BusPaths
from bus.policy import DEDUPE_WINDOW_SECONDS, SYSTEM_SENDER, receipt_for


class FakeClock:
    """可手动推进的单调时钟。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def msg(text="同一句话", *, to="codex", sender="claude"):
    return Message.create(to, text, sender=sender)


# --- 策略本身 -----------------------------------------------------------------


def test_identical_message_within_window_is_rejected():
    clock = FakeClock()
    policy = OutboundPolicy(now=clock)
    first = msg()
    assert policy.check(first).ok
    policy.record(first)

    clock.advance(DEDUPE_WINDOW_SECONDS - 0.1)
    verdict = policy.check(msg())
    assert not verdict.ok
    assert "一模一样" in verdict.reason


def test_same_content_after_window_is_allowed_again():
    clock = FakeClock()
    policy = OutboundPolicy(now=clock)
    policy.record(msg())

    clock.advance(DEDUPE_WINDOW_SECONDS + 0.1)
    assert policy.check(msg()).ok


@pytest.mark.parametrize(
    "variant",
    [
        {"to": "cursor"},  # 换收件人
        {"sender": "agy"},  # 换发件人
        {"text": "换了一句话"},  # 换内容
    ],
)
def test_dedupe_is_scoped_to_sender_recipient_and_text(variant):
    clock = FakeClock()
    policy = OutboundPolicy(now=clock)
    policy.record(msg())
    assert policy.check(msg(**variant)).ok


def test_receipt_targets_sender_and_system_messages_get_no_receipt():
    original = msg()
    receipt = receipt_for(original, "挡下来了")
    assert receipt is not None
    assert receipt.to == "claude"
    assert receipt.sender == SYSTEM_SENDER
    assert receipt.kind == "receipt"
    assert receipt.reply_to == original.id
    assert "挡下来了" in receipt.text
    assert receipt_for(receipt, "回执也被挡") is None


# --- 接进投递循环 -------------------------------------------------------------


def test_hub_drops_duplicate_and_receipts_the_sender(paths):
    clock = FakeClock()
    delivered = []

    def collect(message):
        delivered.append(message.text)
        return True

    hub = Hub(paths, deliver=collect, policy=OutboundPolicy(now=clock))

    deposit(msg("复读机"), paths)
    deposit(msg("复读机"), paths)
    outcomes = [r.outcome for r in hub.drain_once()]

    assert outcomes == [DeliveryOutcome.DELIVERED, DeliveryOutcome.REJECTED]
    assert delivered == ["复读机"]

    # 回执自己也是一条总线消息,还留在队列里等下一轮投递
    queued = [read_message(p) for p in pending(paths)]
    assert [(m.sender, m.to) for m in queued] == [(SYSTEM_SENDER, "claude")]
    assert "未送达" in queued[0].text

    # 下一轮把回执投出去,不会再生出新回执
    assert [r.outcome for r in hub.drain_once()] == [DeliveryOutcome.DELIVERED]
    assert pending(paths) == []
