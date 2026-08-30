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
    TimelineCache,
    TimelineCategory,
    TimelineEntry,
    TimelineProjector,
    by_display_time,
    history,
    history_from_entries,
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
    """T-022(T-017 遗留，opus 复审退回后的场景重写):新调用方接同一个
    `TimelineCache` 才能保证 seq 与缓存的其它使用者一致——只给一个"全新的"
    projector/cache 不够,因为 `history_from_entries` 的批处理到达序(审计
    条目在前、work 事件在后)与缓存长期累积出来的历史到达序可能不同,同一
    个 key 会算出不同的 seq。

    复现条件缺一不可:缓存必须先有历史状态(这里是只有 work 事件、还没有
    任何审计条目时取一次),之后再追加一条审计条目取第二次——两次全新推导
    互相巧合一致的场景(账本状态从头到尾固定不变)测不出这个问题，只能证明
    算法自洽(opus 评审原话)。
    """
    snapshot = _work_snapshot()  # 含 work:event-progress，此时还没有任何审计条目
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    audit = AuditLog(paths)

    cache = TimelineCache()
    _, warm = cache.get(paths, work_events=snapshot.events, snapshot=snapshot)
    event_seq_before = next(e.seq for e in warm if e.key == "work:event-progress")

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

    # 源1:Web `/api/v1/timeline`——缓存有历史状态，再取一次。
    raw_entries, cache_projected = cache.get(
        paths, work_events=snapshot.events, snapshot=snapshot
    )
    cache_seq = {entry.key: entry.seq for entry in cache_projected}
    assert cache_seq["work:event-progress"] == event_seq_before  # 老 key 编号没被挤动

    # 源2:另一个 Web 侧新调用方(比如新开一个 HTTP 端点)——传同一个 cache,
    # 而不是自己另建一个空的 projector。这正是本次修复要保证的路径。
    view = timeline_snapshot_view(
        audit, work_events=snapshot.events, snapshot=snapshot, cache=cache
    )
    view_seq = {entry.key: entry.seq for entry in view.entries}
    assert view_seq == cache_seq

    # 源3:Web `/api/v1/work/tasks/{id}`——复用源1 的 `cache_projected`(真实
    # 接线见 `web/snapshots.py:159-160`)。
    detail = task_detail_view(snapshot, snapshot.tasks[0], raw_entries, timeline=cache_projected)
    assert detail.communications[0].timeline_seq == cache_seq["linked-message"]

    # 反证:不传 cache/projector、各自独立全新推导，会因为到达序不同而分叉
    # ——证明上面的一致不是巧合，是因为真的接到了同一份历史状态。
    fresh_view = timeline_snapshot_view(audit, work_events=snapshot.events, snapshot=snapshot)
    fresh_seq = {entry.key: entry.seq for entry in fresh_view.entries}
    assert fresh_seq != cache_seq
    assert fresh_seq["work:event-progress"] != cache_seq["work:event-progress"]


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
