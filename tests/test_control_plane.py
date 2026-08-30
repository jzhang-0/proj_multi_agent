"""WEB-001：控制面边界、JSON 契约与关键共享投影。"""

from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bus import Message
from bus.audit import AuditEvent, AuditLog
from bus.paths import BusPaths
from control.actions import ControlFeedback
from control.health import Fault, FaultEvent, FaultKind
from control.members import MemberStatusService
from control.tasks import task_board_view, task_detail_view
from control.terminal import terminal_input_rows
from control.timeline import (
    TimelineCategory,
    TimelineEntry,
    TimelineProjector,
    history,
    timeline_snapshot_view,
)
from control.vocabulary import vocabulary
from work import EventKind, Task, TaskStatus, WorkEvent, WorkSnapshot

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "console",
    "fastapi",
    "http",
    "rich",
    "starlette",
    "textual",
}


def _assert_json_dto(value: object) -> dict[str, object]:
    assert is_dataclass(value)
    _assert_transport_types(value)
    payload = asdict(value)  # type: ignore[arg-type]
    json.dumps(payload)
    return payload


def _assert_transport_types(value: object) -> None:
    assert not isinstance(value, (Counter, datetime, Path))
    assert type(value).__module__.partition(".")[0] not in {"rich", "textual"}
    if is_dataclass(value):
        for field in fields(value):
            _assert_transport_types(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_transport_types(key)
            _assert_transport_types(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_transport_types(item)


def _work_snapshot() -> WorkSnapshot:
    task = Task(
        id="T-003",
        title="控制\x1b[31m面",
        description="共享读模型",
        leader="fable",
        parent_id=None,
        status=TaskStatus.IN_PROGRESS,
        created_at="2026-08-30T05:00:00Z",
        updated_at="2026-08-30T06:30:00Z",
        assignee="sol",
        reviewer="opus",
        evidence=("tests/test_control_plane.py",),
        latest="实现中",
    )
    event = WorkEvent(
        1,
        1,
        "event-progress",
        task.id,
        EventKind.PROGRESS,
        "sol",
        task.updated_at,
        {"summary": "完成\x1b[2J投影", "internal_secret": "不得出 DTO"},
        "",
        "hash-1",
    )
    return WorkSnapshot((task,), (event,))


def test_control_source_has_no_ui_or_http_imports() -> None:
    failures: list[str] = []
    for path in sorted((ROOT / "src" / "control").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            roots: set[str] = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.partition(".")[0]}
            forbidden = roots & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}:{sorted(forbidden)}")
    assert failures == []


def test_member_dto_uses_silent_duration_and_epoch_snapshot_not_monotonic() -> None:
    monotonic = [100.0]
    service = MemberStatusService(
        ("sol",),
        clock=lambda: monotonic[0],
        wall_clock=lambda: 1_756_512_034.5,
        sources={"sol": "adopted"},
    )
    service.record_output("sol", "有输出")
    monotonic[0] = 103.5

    view = service.snapshot_view({"sol": 2})
    snapshot = view.members[0]
    payload = _assert_json_dto(snapshot)

    assert payload == {
        "name": "sol",
        "state": "idle",
        "queued": 2,
        "silent_for": 3.5,
        "alive": True,
        "source": "adopted",
    }
    assert _assert_json_dto(view)["snapshot_at"] == 1_756_512_034.5
    assert "last_output_at" not in {field.name for field in fields(snapshot)}


def test_task_dtos_normalize_times_filter_event_data_and_link_audit(tmp_path: Path) -> None:
    snapshot = _work_snapshot()
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    linked = Message.create(
        "sol",
        "请处理\x1b[31m",
        sender="fable",
        task="T-003",
        ts="2026-08-30 14:20:00",
    )
    audit.record(AuditEvent.DEPOSIT, linked)
    audit.record(
        AuditEvent.DEPOSIT,
        Message.create("opus", "无关", sender="fable", task="T-999"),
    )

    board = task_board_view(snapshot, "fable")
    detail = task_detail_view(snapshot, snapshot.tasks[0], audit.entries())
    board_payload = _assert_json_dto(board)
    detail_payload = _assert_json_dto(detail)

    assert board.selected_default == "T-003"
    assert board.summary.by_status["in-progress"] == 1
    assert board_payload["tasks"][0]["created_at"] == pytest.approx(
        datetime(2026, 8, 30, 5, tzinfo=UTC).timestamp()
    )
    assert detail.events[0].details == {"summary": "完成投影"}
    assert detail.communications[0].text == "请处理"
    assert detail.communications[0].at > 1_000_000_000
    assert "internal_secret" not in json.dumps(detail_payload, ensure_ascii=False)


def test_timeline_assigns_cursor_key_epoch_and_preserves_raw_timestamp(tmp_path: Path) -> None:
    snapshot = _work_snapshot()
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    message = Message.create(
        "sol",
        "推进 T-003",
        sender="fable",
        message_id="message-1",
        ts="2026-08-30 14:20:00",
        task="T-003",
    )
    audit.record(AuditEvent.DEPOSIT, message)
    audit.record(AuditEvent.DELIVER, message)

    entries = history(audit, work_events=snapshot.events, snapshot=snapshot)
    view = timeline_snapshot_view(
        audit,
        work_events=snapshot.events,
        snapshot=snapshot,
    )

    message_entry = next(entry for entry in entries if entry.key == "message-1")
    task_entry = next(
        entry for entry in entries if entry.category is TimelineCategory.TASK
    )
    assert [entry.seq for entry in entries] == [1, 2]
    assert message_entry.ts == audit.entries()[0]["ts"]
    assert message_entry.at > 1_000_000_000
    assert task_entry.at == pytest.approx(
        datetime(2026, 8, 30, 6, 30, tzinfo=UTC).timestamp()
    )
    assert view.category_counts == {
        "all": 2,
        "human": 0,
        "ai": 1,
        "task": 1,
        "control": 0,
    }
    projector = TimelineProjector()
    projector.seed(entries)
    live = projector.project(
        TimelineEntry(
            "2026-08-30 17:00:00",
            "opus",
            "sol",
            "评审",
            key="message-live",
        )
    )
    update = projector.project(
        TimelineEntry(
            live.ts,
            live.sender,
            live.to,
            live.text,
            outcome="delivered",
            key=live.key,
        )
    )
    assert live.seq == update.seq == 3
    for entry in entries:
        _assert_json_dto(entry)
    _assert_json_dto(view)


def test_all_public_leaf_dtos_are_json_safe_and_terminal_rule_is_shared() -> None:
    _assert_json_dto(ControlFeedback("interrupt", "sol", True, "ok"))
    fault = _assert_json_dto(
        FaultEvent(Fault(FaultKind.MEMBER_SESSION, "sol", "会话消失"))
    )
    assert fault["fault"]["key"] == "member-session:sol"
    _assert_json_dto(vocabulary())

    screen = "输出\n──────────\n输入中\n──────────\n? for shortcuts"
    assert terminal_input_rows(screen) == (2,)
