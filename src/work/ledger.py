"""工作区任务事件账本：加锁追加、哈希链校验与状态投影。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from work.model import (
    EventKind,
    LedgerCorruptionError,
    Task,
    TaskStatus,
    WorkEvent,
    WorkSnapshot,
    WorkValidationError,
    require_text,
    validate_task_id,
)
from workspace.model import Workspace

LEDGER_VERSION = 1
GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class PendingEvent:
    """事务回调返回的、尚未编号和盖哈希的事件。"""

    task_id: str
    kind: EventKind
    actor: str
    data: dict[str, Any]


class WorkLedger:
    """只通过追加事件改变状态；读取时验证整条链。"""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.root = workspace.state_dir / "work"
        self.path = self.root / "events.jsonl"
        self.lock_path = self.root / ".events.lock"

    def load(self) -> WorkSnapshot:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return project(self._read_unlocked())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def transact(
        self,
        build: Callable[[WorkSnapshot], PendingEvent | Iterable[PendingEvent]],
    ) -> tuple[WorkEvent, ...]:
        """在同一把进程锁内读取、校验并追加，防止任务编号和状态竞态。"""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                existing = self._read_unlocked()
                snapshot = project(existing)
                pending = build(snapshot)
                items = (pending,) if isinstance(pending, PendingEvent) else tuple(pending)
                if not items:
                    raise WorkValidationError("事务没有产生事件")
                created: list[WorkEvent] = []
                previous = existing[-1].digest if existing else GENESIS_HASH
                seq = existing[-1].seq if existing else 0
                for item in items:
                    seq += 1
                    event = _seal(item, seq=seq, previous=previous)
                    created.append(event)
                    previous = event.digest
                # 先重放整个候选序列，确保不会把无法读取的状态写入账本。
                project((*existing, *created))
                with self.path.open("a", encoding="utf-8") as handle:
                    for event in created:
                        handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return tuple(created)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> tuple[WorkEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[WorkEvent] = []
        previous = GENESIS_HASH
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    event = _event_from_dict(raw)
                except (json.JSONDecodeError, WorkValidationError, TypeError, ValueError) as exc:
                    raise LedgerCorruptionError(
                        f"账本 {self.path} 第 {line_number} 行无效: {exc}"
                    ) from exc
                expected_seq = len(events) + 1
                if event.seq != expected_seq:
                    raise LedgerCorruptionError(
                        f"账本 {self.path} 第 {line_number} 行序号应为 {expected_seq}"
                    )
                if event.previous != previous:
                    raise LedgerCorruptionError(
                        f"账本 {self.path} 第 {line_number} 行前序哈希不匹配"
                    )
                expected_hash = _digest(_unsigned(event))
                if event.digest != expected_hash:
                    raise LedgerCorruptionError(
                        f"账本 {self.path} 第 {line_number} 行哈希校验失败，历史可能被改写"
                    )
                events.append(event)
                previous = event.digest
        return tuple(events)


def project(events: Iterable[WorkEvent]) -> WorkSnapshot:
    """把事件流重放成任务投影；投影只存在内存中。"""
    tasks: dict[str, Task] = {}
    ordered_events: list[WorkEvent] = []
    for event in events:
        ordered_events.append(event)
        if event.kind in (EventKind.CREATED, EventKind.SPLIT):
            if event.task_id in tasks:
                raise LedgerCorruptionError(f"任务 {event.task_id} 被重复创建")
            parent = event.data.get("parent")
            if event.kind is EventKind.SPLIT:
                if not isinstance(parent, str) or parent not in tasks:
                    raise LedgerCorruptionError(f"拆分任务 {event.task_id} 缺少有效父任务")
            elif parent is not None:
                raise LedgerCorruptionError("普通创建事件不能带 parent")
            tasks[event.task_id] = Task(
                id=event.task_id,
                title=_data_text(event, "title"),
                description=str(event.data.get("description", "")),
                leader=_data_text(event, "leader"),
                parent_id=parent,
                status=TaskStatus.BACKLOG,
                created_at=event.ts,
                updated_at=event.ts,
                event_ids=(event.id,),
            )
            continue
        try:
            task = tasks[event.task_id]
        except KeyError as exc:
            raise LedgerCorruptionError(
                f"事件 {event.id} 引用了尚未创建的任务 {event.task_id}"
            ) from exc
        changes: dict[str, Any] = {
            "updated_at": event.ts,
            "event_ids": (*task.event_ids, event.id),
        }
        if event.kind in (EventKind.ASSIGNED, EventKind.REASSIGNED):
            changes.update(
                status=TaskStatus.ASSIGNED,
                assignee=_data_text(event, "assignee"),
                reviewer=None,
                latest=str(event.data.get("reason", "")),
            )
        elif event.kind is EventKind.PROGRESS:
            changes.update(status=TaskStatus.IN_PROGRESS, latest=_data_text(event, "summary"))
        elif event.kind is EventKind.BLOCKED:
            changes.update(status=TaskStatus.BLOCKED, latest=_data_text(event, "reason"))
        elif event.kind is EventKind.EVIDENCE:
            reference = _data_text(event, "reference")
            changes.update(evidence=(*task.evidence, reference), latest=reference)
        elif event.kind is EventKind.SUBMITTED:
            changes.update(status=TaskStatus.SUBMITTED, latest=_data_text(event, "summary"))
        elif event.kind is EventKind.REVIEW_REQUESTED:
            changes.update(
                status=TaskStatus.IN_REVIEW,
                reviewer=_data_text(event, "reviewer"),
                latest=str(event.data.get("note", "")),
            )
        elif event.kind is EventKind.REVIEW_PASSED:
            changes.update(status=TaskStatus.REVIEWED, latest=_data_text(event, "note"))
        elif event.kind is EventKind.REVIEW_RETURNED:
            changes.update(
                status=TaskStatus.CHANGES_REQUESTED,
                latest=_data_text(event, "reason"),
            )
        elif event.kind is EventKind.TAKEOVER:
            changes.update(
                status=TaskStatus.IN_PROGRESS,
                assignee=task.leader,
                reviewer=None,
                latest=_data_text(event, "reason"),
            )
        elif event.kind is EventKind.ACCEPTED:
            changes.update(
                status=TaskStatus.ACCEPTED,
                latest=_data_text(event, "conclusion"),
                accepted_by=event.actor,
            )
        elif event.kind is EventKind.REPORTED:
            changes.update(status=TaskStatus.COMPLETED, latest=_data_text(event, "summary"))
        else:  # pragma: no cover - StrEnum 的穷举保护
            raise LedgerCorruptionError(f"不支持的事件类型 {event.kind}")
        tasks[event.task_id] = replace(task, **changes)
    return WorkSnapshot(tasks=tuple(tasks.values()), events=tuple(ordered_events))


def _seal(pending: PendingEvent, *, seq: int, previous: str) -> WorkEvent:
    validate_task_id(pending.task_id)
    actor = require_text(pending.actor, "actor")
    unsigned = {
        "v": LEDGER_VERSION,
        "seq": seq,
        "id": uuid.uuid4().hex,
        "task": pending.task_id,
        "type": str(pending.kind),
        "actor": actor,
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "data": pending.data,
        "prev": previous,
    }
    # 在落盘前确认 data 能稳定序列化；禁止 NaN 这类非标准 JSON 值。
    json.dumps(unsigned, ensure_ascii=False, allow_nan=False)
    return WorkEvent(
        version=LEDGER_VERSION,
        seq=seq,
        id=str(unsigned["id"]),
        task_id=pending.task_id,
        kind=pending.kind,
        actor=actor,
        ts=str(unsigned["ts"]),
        data=dict(pending.data),
        previous=previous,
        digest=_digest(unsigned),
    )


def _unsigned(event: WorkEvent) -> dict[str, Any]:
    payload = event.to_dict()
    payload.pop("hash")
    return payload


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_from_dict(raw: Any) -> WorkEvent:
    if not isinstance(raw, dict):
        raise WorkValidationError("事件必须是 JSON 对象")
    expected = {"v", "seq", "id", "task", "type", "actor", "ts", "data", "prev", "hash"}
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        extra = sorted(set(raw) - expected)
        raise WorkValidationError(f"事件字段不匹配，缺少 {missing}，多出 {extra}")
    if raw["v"] != LEDGER_VERSION:
        raise WorkValidationError(f"不支持的账本版本 {raw['v']!r}")
    if not isinstance(raw["seq"], int) or raw["seq"] < 1:
        raise WorkValidationError("事件 seq 必须是正整数")
    if not isinstance(raw["data"], dict):
        raise WorkValidationError("事件 data 必须是对象")
    try:
        kind = EventKind(raw["type"])
    except ValueError as exc:
        raise WorkValidationError(f"未知事件类型 {raw['type']!r}") from exc
    return WorkEvent(
        version=LEDGER_VERSION,
        seq=raw["seq"],
        id=require_text(raw["id"], "事件 id"),
        task_id=validate_task_id(raw["task"]),
        kind=kind,
        actor=require_text(raw["actor"], "actor"),
        ts=require_text(raw["ts"], "时间戳"),
        data=dict(raw["data"]),
        previous=require_text(raw["prev"], "前序哈希"),
        digest=require_text(raw["hash"], "事件哈希"),
    )


def _data_text(event: WorkEvent, key: str) -> str:
    try:
        value = event.data[key]
    except KeyError as exc:
        raise LedgerCorruptionError(f"事件 {event.id} 缺少 {key}") from exc
    try:
        return require_text(value, key)
    except WorkValidationError as exc:
        raise LedgerCorruptionError(f"事件 {event.id} 的 {key} 无效") from exc
