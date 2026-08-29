"""TEAM-002：总线的可选 task 字段关联沟通，但不改变旧四字段契约。"""

from __future__ import annotations

import io

from bus import format_for_injection, pending, read_message
from bus.audit import AuditLog
from bus.cli import main as msg_main
from bus.paths import BusPaths


def test_task_flag_survives_queue_audit_and_member_injection(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    out = io.StringIO()
    assert (
        msg_main(
            ["--task", "T-024", "sonnet", "检查登录页"],
            paths=paths,
            sender="fable",
            stdout=out,
        )
        == 0
    )
    message = read_message(pending(paths)[0])
    assert message.task == "T-024"
    assert message.extra == {}
    assert "任务 T-024 · 群消息" in format_for_injection(message)
    assert 'amux msg --task T-024 fable "你的回复"' in format_for_injection(message)
    assert AuditLog(paths).entries()[0]["task"] == "T-024"
    assert "T-024" in out.getvalue()


def test_invalid_task_id_is_rejected_without_queueing(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    err = io.StringIO()
    assert (
        msg_main(
            ["--task", "oops", "sonnet", "不应入队"],
            paths=paths,
            sender="fable",
            stderr=err,
        )
        == 2
    )
    assert "必须形如 T-001" in err.getvalue()
    assert pending(paths) == []
