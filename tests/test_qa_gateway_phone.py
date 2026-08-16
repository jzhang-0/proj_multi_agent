"""GATE-003 存档证据的离线完成契约。"""

import copy

import pytest

from qa.gateway_phone import RUN_TAG, validate_record


def record():
    user = "im:验收人"
    edges = (
        (user, "cursor"),
        ("cursor", "agy"),
        ("agy", "cursor"),
        ("cursor", user),
    )
    audit = [
        {"event": event, "from": sender, "to": recipient, "preview": RUN_TAG}
        for sender, recipient in edges
        for event in ("deposit", "deliver")
    ]
    transcript = [
        {"author": sender, "text": f"→ {recipient}: {RUN_TAG}"}
        for sender, recipient in edges
    ]
    return {
        "goal": "GATE-003",
        "run_tag": RUN_TAG,
        "participants": {
            "remote_user": "验收人",
            "coordinator": "cursor",
            "collaborator": "agy",
        },
        "human_attestation": {
            "device": "physical phone browser",
            "computer_untouched": True,
            "confirmed_at": "2026-08-16T14:30:00+08:00",
        },
        "audit": audit,
        "transcript": transcript,
    }


def test_complete_phone_collaboration_record_passes():
    assert validate_record(record()) == (8, 4)


@pytest.mark.parametrize("event", ["deposit", "deliver"])
def test_every_direction_requires_both_audit_events(event):
    sample = record()
    sample["audit"] = [
        item
        for item in sample["audit"]
        if not (item["from"] == "agy" and item["to"] == "cursor" and item["event"] == event)
    ]
    with pytest.raises(ValueError, match=f"缺少 {event} 边:agy->cursor"):
        validate_record(sample)


def test_phone_attestation_is_mandatory():
    sample = record()
    sample["human_attestation"]["computer_untouched"] = False
    with pytest.raises(ValueError, match="全程不碰电脑"):
        validate_record(sample)


def test_two_distinct_members_are_mandatory():
    sample = record()
    sample["participants"]["collaborator"] = "cursor"
    with pytest.raises(ValueError, match="两个真实成员"):
        validate_record(sample)


def test_visible_group_posts_must_be_complete_and_ordered():
    sample = record()
    sample["transcript"][1], sample["transcript"][2] = (
        sample["transcript"][2],
        sample["transcript"][1],
    )
    with pytest.raises(ValueError, match="顺序不对"):
        validate_record(sample)


def test_archive_validation_does_not_mutate_record():
    sample = record()
    before = copy.deepcopy(sample)
    validate_record(sample)
    assert sample == before
