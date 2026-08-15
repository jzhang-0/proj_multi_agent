"""QA-003:不依赖 tmux 的防环策略契约矩阵。"""

from bus import Message, OutboundPolicy
from bus.policy import BACKLOG_CAP, MAX_TEXT_BYTES, RATE_LIMIT_MAX


def message(index: int, *, sender: str = "claude", text: str | None = None) -> Message:
    body = text if text is not None else f"消息 {index}"
    return Message.create("cursor", body, sender=sender)


def accept(policy: OutboundPolicy, candidate: Message) -> None:
    assert policy.check(candidate).ok
    policy.record(candidate)


def test_duplicate_content_is_rejected() -> None:
    policy = OutboundPolicy(now=lambda: 0.0)
    first = message(0, text="复读内容")
    accept(policy, first)

    verdict = policy.check(message(1, text="复读内容"))

    assert not verdict.ok
    assert "一模一样" in verdict.reason


def test_ninth_ai_message_is_rate_limited() -> None:
    policy = OutboundPolicy(now=lambda: 0.0)
    for index in range(RATE_LIMIT_MAX):
        accept(policy, message(index))

    verdict = policy.check(message(RATE_LIMIT_MAX))

    assert not verdict.ok
    assert "最多发送 8 条" in verdict.reason


def test_recipient_backlog_trips_circuit_breaker() -> None:
    verdict = OutboundPolicy().check(message(0), unread_backlog=BACKLOG_CAP)

    assert not verdict.ok
    assert "对方积压中" in verdict.reason


def test_oversize_message_has_actionable_rejection() -> None:
    oversized = message(0, text="x" * (MAX_TEXT_BYTES + 1))

    verdict = OutboundPolicy().check(oversized)

    assert not verdict.ok
    assert "发摘要和路径,不要发内容" in verdict.reason


def test_human_sender_is_exempt_from_rate_limit() -> None:
    policy = OutboundPolicy(now=lambda: 0.0)

    for index in range(RATE_LIMIT_MAX * 2):
        accept(policy, message(index, sender="human"))
