"""QA-005 存档证据可离线重复验证。"""

from qa.collab import validate


def test_archived_real_collaboration_evidence() -> None:
    message_events, controls = validate()
    assert message_events >= 16  # 8 条有向边，每条 deposit + deliver
    assert controls >= 1
