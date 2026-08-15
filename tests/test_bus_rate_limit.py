"""BUS-003:每个 AI 发件人的滑动窗口限频与回执。"""

from bus import BusPaths, DeliveryOutcome, Hub, Message, deposit, pending, read_message
from bus.policy import (
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW_SECONDS,
    OutboundPolicy,
    receipt_for,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def msg(index: int, *, sender: str = "claude") -> Message:
    return Message.create("codex", f"消息 {index}", sender=sender)


def send(policy: OutboundPolicy, message: Message) -> None:
    assert policy.check(message).ok
    policy.record(message)


def test_ninth_ai_message_in_thirty_seconds_is_rejected_with_receipt_reason():
    policy = OutboundPolicy(now=FakeClock())
    for index in range(RATE_LIMIT_MAX):
        send(policy, msg(index))

    rejected = msg(RATE_LIMIT_MAX)
    verdict = policy.check(rejected)
    assert not verdict.ok
    assert "30 秒" in verdict.reason
    assert "8 条" in verdict.reason
    receipt = receipt_for(rejected, verdict.reason)
    assert receipt is not None
    assert receipt.to == rejected.sender
    assert verdict.reason in receipt.text


def test_rate_limit_is_scoped_per_sender():
    policy = OutboundPolicy(now=FakeClock())
    for index in range(RATE_LIMIT_MAX):
        send(policy, msg(index, sender="claude"))

    assert policy.check(msg(99, sender="cursor")).ok
    assert not policy.check(msg(99, sender="claude")).ok


def test_quota_recovers_at_window_boundary():
    clock = FakeClock()
    policy = OutboundPolicy(now=clock)
    for index in range(RATE_LIMIT_MAX):
        send(policy, msg(index))

    clock.advance(RATE_LIMIT_WINDOW_SECONDS)
    assert policy.check(msg(99)).ok


def test_human_is_exempt_and_does_not_consume_a_rate_bucket():
    policy = OutboundPolicy(now=FakeClock())
    for index in range(RATE_LIMIT_MAX * 3):
        send(policy, msg(index, sender="human"))

    assert policy.check(msg(999, sender="human")).ok


def test_rejected_attempt_does_not_consume_future_quota():
    clock = FakeClock()
    policy = OutboundPolicy(now=clock, rate_limit=1)
    send(policy, msg(0))
    assert not policy.check(msg(1)).ok

    clock.advance(RATE_LIMIT_WINDOW_SECONDS)
    send(policy, msg(2))
    assert not policy.check(msg(3)).ok


def test_hub_rejects_ninth_ai_message_and_queues_receipt(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    messages = [msg(index) for index in range(RATE_LIMIT_MAX + 1)]
    for message in messages:
        deposit(message, paths)

    delivered = []

    def collect(message):
        delivered.append(message)
        return True

    hub = Hub(paths, deliver=collect, policy=OutboundPolicy(now=FakeClock()))
    results = hub.drain_once()

    assert [result.outcome for result in results] == [
        *[DeliveryOutcome.DELIVERED] * RATE_LIMIT_MAX,
        DeliveryOutcome.REJECTED,
    ]
    assert delivered == messages[:RATE_LIMIT_MAX]

    queued = [read_message(path) for path in pending(paths)]
    assert len(queued) == 1
    assert queued[0].sender == "bus"
    assert queued[0].to == "claude"
    assert queued[0].kind == "receipt"
    assert queued[0].reply_to == messages[-1].id
    assert "30 秒" in queued[0].text

    assert [result.outcome for result in hub.drain_once()] == [DeliveryOutcome.DELIVERED]
    assert delivered[-1] == queued[0]


def test_hub_counts_ai_messages_addressed_to_human(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    for index in range(RATE_LIMIT_MAX + 1):
        deposit(Message.create("human", f"汇报 {index}", sender="claude"), paths)

    def never_deliver(message):
        raise AssertionError(f"发给 human 的消息不应调用 deliver:{message.text}")

    results = Hub(
        paths,
        deliver=never_deliver,
        policy=OutboundPolicy(now=FakeClock()),
    ).drain_once()
    assert [result.outcome for result in results] == [
        *[DeliveryOutcome.SHOWN] * RATE_LIMIT_MAX,
        DeliveryOutcome.REJECTED,
    ]


def test_hub_does_not_limit_human_sender(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    for index in range(RATE_LIMIT_MAX + 1):
        deposit(msg(index, sender="human"), paths)

    delivered = []

    def collect(message):
        delivered.append(message)
        return True

    results = Hub(paths, deliver=collect, policy=OutboundPolicy(now=FakeClock())).drain_once()

    assert [result.outcome for result in results] == [DeliveryOutcome.DELIVERED] * (
        RATE_LIMIT_MAX + 1
    )
    assert len(delivered) == RATE_LIMIT_MAX + 1
