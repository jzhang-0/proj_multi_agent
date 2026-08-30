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
    HISTORY_LIMIT,
    TimelineCategory,
    TimelineEntry,
    TimelineProjector,
    by_display_time,
    history,
    history_from_entries,
    timeline_snapshot_view,
)
from control.vocabulary import vocabulary
from web.state import TimelineCache
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


def test_task_communication_seq_matches_merged_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _work_snapshot()
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    first = Message.create(
        "sol",
        "第一条无关消息",
        sender="fable",
        message_id="noise-1",
        ts="2026-08-30 13:00:00",
    )
    second = Message.create(
        "sol",
        "第二条无关消息",
        sender="fable",
        message_id="noise-2",
        ts="2026-08-30 14:00:00",
    )
    linked = Message.create(
        "sol",
        "关联 T-003",
        sender="fable",
        message_id="linked-message",
        ts="2026-08-30 15:00:00",
        task="T-003",
    )
    audit_times = iter(
        (
            "2026-08-30 13:00:00",
            "2026-08-30 13:00:01",
            "2026-08-30 14:00:00",
            "2026-08-30 14:00:01",
            "2026-08-30 15:00:00",
            "2026-08-30 15:00:01",
        )
    )
    monkeypatch.setattr("bus.audit.time.strftime", lambda _format: next(audit_times))
    for message in (first, second, linked):
        audit.record(AuditEvent.DEPOSIT, message)
        audit.record(AuditEvent.DELIVER, message)

    entries = history(audit, work_events=snapshot.events, snapshot=snapshot)
    detail = task_detail_view(snapshot, snapshot.tasks[0], audit.entries())
    linked_entry = next(entry for entry in entries if entry.key == linked.id)

    assert [entry.key for entry in entries] == [
        "noise-1",
        "noise-2",
        "linked-message",
        "work:event-progress",
    ]
    assert [entry.seq for entry in entries] == [1, 2, 3, 4]
    assert detail.communications[0].timeline_seq == linked_entry.seq == 3
    assert [entry.key for entry in by_display_time(entries)] == [
        "noise-1",
        "noise-2",
        "work:event-progress",
        "linked-message",
    ]


def test_timeline_seq_agrees_across_snapshot_view_cache_and_task_detail(
    tmp_path: Path,
) -> None:
    """T-022(T-017 遗留):同一账本状态下,三条各自独立的读取路径必须给出同一个
    seq——TUI 的 `timeline_snapshot_view`(接自己持有的 `TimelineProjector`,
    对齐 `ConsoleApp.timeline_projector` 的真实用法)、Web `/api/v1/timeline`
    背后的 `TimelineCache`、以及 Web `/api/v1/work/tasks/{id}` 的
    `communications.timeline_seq`(复用 `TimelineCache.get()` 投影结果，同
    `web/snapshots.py` 的真实接线)。三者都用各自生产路径上真实持有的对象
    取值，不是三次独立调用 `history_from_entries` 后比对结果——那只能证明
    算法自洽，证明不了确实同源(opus 评审意见)。
    """
    snapshot = _work_snapshot()
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    linked = Message.create(
        "sol",
        "关联 T-003",
        sender="fable",
        message_id="linked-message",
        task="T-003",
        ts="2026-08-30 15:00:00",
    )
    audit.record(AuditEvent.DEPOSIT, linked)
    audit.record(AuditEvent.DELIVER, linked)

    # 源1:TUI——`timeline_snapshot_view` 接自己的 `TimelineProjector`。
    tui_projector = TimelineProjector()
    tui_view = timeline_snapshot_view(
        audit,
        work_events=snapshot.events,
        snapshot=snapshot,
        projector=tui_projector,
    )
    tui_entry = next(entry for entry in tui_view.entries if entry.key == linked.id)

    # 源2:Web `/api/v1/timeline`——`TimelineCache.get()`。
    cache = TimelineCache()
    raw_entries, projected = cache.get(
        paths, work_events=snapshot.events, snapshot=snapshot
    )
    web_entry = next(entry for entry in projected if entry.key == linked.id)

    # 源3:Web `/api/v1/work/tasks/{id}`——复用源2 的 `projected`(真实接线见
    # `web/snapshots.py:159-160`)。
    detail = task_detail_view(snapshot, snapshot.tasks[0], raw_entries, timeline=projected)

    assert tui_entry.seq == web_entry.seq == detail.communications[0].timeline_seq


def test_history_window_preserves_full_timeline_seq(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    for index in range(HISTORY_LIMIT + 5):
        audit.record(
            AuditEvent.DEPOSIT,
            Message.create(
                "sol",
                "",
                sender="fable",
                message_id=f"message-{index}",
                ts="2026-08-30 14:20:00",
            ),
        )

    backfill = history(audit)
    snapshot_view = timeline_snapshot_view(audit)
    snapshot_by_key = {entry.key: entry for entry in snapshot_view.entries}

    assert backfill[0].seq == snapshot_by_key[backfill[0].key].seq == 6
    assert snapshot_view.head_seq == HISTORY_LIMIT + 5
    assert snapshot_view.oldest_seq == 6
    assert snapshot_view.has_more
    projector = TimelineProjector()
    projector.seed(list(snapshot_view.entries))
    assert projector.project(backfill[0]).seq == backfill[0].seq


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


def test_seq_follows_arrival_and_survives_earlier_at_insert(tmp_path: Path) -> None:
    """后到、更早 at 的任务事件拿下一个 seq，不得挤占已有 key。"""
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)
    first = Message.create(
        "sol",
        "先到",
        sender="fable",
        message_id="arrived-first",
        ts="2026-08-30 18:00:00",
    )
    audit.record(AuditEvent.DEPOSIT, first)
    projector = TimelineProjector()
    initial = history_from_entries(audit.entries(), projector=projector)
    assert [entry.key for entry in initial] == ["arrived-first"]
    assert initial[0].seq == 1

    task = Task(
        id="T-010",
        title="插到前面",
        description="",
        leader="fable",
        parent_id=None,
        status=TaskStatus.IN_PROGRESS,
        created_at="2020-01-01T00:00:00Z",
        updated_at="2020-01-01T00:00:00Z",
        assignee="sol",
        reviewer="",
        evidence=(),
        latest="",
    )
    early = WorkEvent(
        1,
        1,
        "event-early",
        task.id,
        EventKind.PROGRESS,
        "sol",
        "2020-01-01T00:00:00Z",
        {"summary": "更早的 at"},
        "",
        "hash-early",
    )
    snapshot = WorkSnapshot((task,), (early,))
    rebuilt = history_from_entries(
        audit.entries(),
        work_events=snapshot.events,
        snapshot=snapshot,
        projector=projector,
    )
    by_key = {entry.key: entry for entry in rebuilt}
    assert by_key["arrived-first"].seq == 1
    assert by_key["work:event-early"].seq == 2
    assert [entry.key for entry in by_display_time(rebuilt)] == [
        "work:event-early",
        "arrived-first",
    ]


def test_all_public_leaf_dtos_are_json_safe_and_terminal_rule_is_shared() -> None:
    _assert_json_dto(ControlFeedback("interrupt", "sol", True, "ok"))
    fault = _assert_json_dto(
        FaultEvent(Fault(FaultKind.MEMBER_SESSION, "sol", "会话消失"))
    )
    assert fault["fault"]["key"] == "member-session:sol"
    _assert_json_dto(vocabulary())

    screen = "输出\n──────────\n输入中\n──────────\n? for shortcuts"
    assert terminal_input_rows(screen) == (2,)
