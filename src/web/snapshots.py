"""只读 snapshot DTO 组装(docs/web/api-protocol.md §4)：只调用控制面 + 领域层，
不建路由、不管鉴权(由 web.app 负责)，不产生任何副作用(不 resize、不 send_keys)。
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from bus.audit import AuditLog
from bus.sanitize import sanitize
from control.health import HealthMonitor
from control.members import MemberStatusService, pending_counts
from control.tasks import task_board_view, task_detail_view
from control.timeline import HISTORY_LIMIT, TimelineCategory, TimelineEntry, from_audit
from control.vocabulary import vocabulary as build_vocabulary
from team.model import TeamValidationError
from team.store import TeamNotFound
from web.context import SnapshotContext, load_bound_team, require_paths, require_workspace
from web.errors import ApiError
from web.state import RevisionTracker, TimelineCache
from work import WorkError, WorkSnapshot, WorkValidationError
from work.model import validate_task_id
from work.service import WorkService
from workspace.errors import WorkspaceError
from workspace.model import Workspace


def _work_service(workspace: Workspace) -> WorkService:
    try:
        return WorkService.for_workspace(workspace)
    except WorkValidationError as exc:
        if "尚未绑定团队" in str(exc):
            raise ApiError("team-unbound", str(exc), status_code=409, domain="work") from exc
        raise ApiError("work-unavailable", str(exc), status_code=503, domain="work") from exc
    except (WorkError, OSError) as exc:
        raise ApiError("work-unavailable", str(exc), status_code=503, domain="work") from exc


def _work_snapshot(service: WorkService) -> WorkSnapshot:
    try:
        return service.snapshot()
    except (WorkError, OSError) as exc:
        raise ApiError("work-unavailable", str(exc), status_code=503, domain="work") from exc


def _optional_work_snapshot(workspace: Workspace) -> WorkSnapshot | None:
    """任务账本未绑定/损坏时静默降级为 None，不阻塞时间线/成员/健康这些不依赖它的域。"""
    try:
        service = WorkService.for_workspace(workspace)
        return service.snapshot()
    except (WorkError, OSError):
        return None


def vocabulary_dto(tracker: RevisionTracker) -> dict[str, Any]:
    payload = asdict(build_vocabulary())
    payload["epoch"] = tracker.epoch
    return payload


def workspace_dto(ctx: SnapshotContext, tracker: RevisionTracker) -> dict[str, Any]:
    if ctx.workspace is None:
        revision = tracker.observe("workspace", None)
        return {
            "epoch": tracker.epoch,
            "revision": revision,
            "registered": False,
            "slug": None,
            "project_root": None,
        }
    fingerprint = (ctx.workspace.slug, str(ctx.workspace.project_root))
    revision = tracker.observe("workspace", fingerprint)
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "registered": True,
        "slug": ctx.workspace.slug,
        "project_root": str(ctx.workspace.project_root),
    }


def team_dto(ctx: SnapshotContext, tracker: RevisionTracker) -> dict[str, Any]:
    if ctx.workspace is None:
        revision = tracker.observe("team", None)
        return {"epoch": tracker.epoch, "revision": revision, "bound": False}
    try:
        _binding, team = load_bound_team(ctx.workspace)
    except (TeamNotFound, TeamValidationError, WorkspaceError) as exc:
        raise ApiError("work-unavailable", str(exc), status_code=503, domain="team") from exc
    if team is None:
        revision = tracker.observe("team", None)
        return {"epoch": tracker.epoch, "revision": revision, "bound": False}
    members = [
        {
            "id": member.id,
            "role": member.role,
            "model": member.model,
            "effort": member.effort,
            "speed": member.speed,
            "responsibility": sanitize(member.responsibility),
        }
        for member in team.members
    ]
    fingerprint = (
        team.id,
        sanitize(team.name),
        sanitize(team.description),
        sanitize(team.leader),
        tuple(tuple(sorted(item.items())) for item in members),
    )
    revision = tracker.observe("team", fingerprint)
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "bound": True,
        "id": team.id,
        "name": sanitize(team.name),
        "description": sanitize(team.description),
        "leader": sanitize(team.leader),
        "members": members,
    }


def work_dto(ctx: SnapshotContext, tracker: RevisionTracker) -> dict[str, Any]:
    workspace = require_workspace(ctx)
    service = _work_service(workspace)
    snapshot = _work_snapshot(service)
    board = task_board_view(snapshot, service.team.leader)
    fingerprint = snapshot.events[-1].digest if snapshot.events else None
    revision = tracker.observe("work", fingerprint)
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "summary": asdict(board.summary),
        "tasks": [asdict(item) for item in board.tasks],
        "selected_default": board.selected_default,
    }


def task_detail_dto(
    ctx: SnapshotContext,
    tracker: RevisionTracker,
    cache: TimelineCache,
    task_id: str,
) -> dict[str, Any]:
    workspace = require_workspace(ctx)
    paths = require_paths(ctx)
    try:
        validate_task_id(task_id)
    except WorkValidationError as exc:
        raise ApiError("invalid-request", str(exc), status_code=400, domain="work") from exc
    service = _work_service(workspace)
    snapshot = _work_snapshot(service)
    task = next((item for item in snapshot.tasks if item.id == task_id), None)
    if task is None:
        raise ApiError("not-found", f"任务 {task_id} 不存在", status_code=404, domain="work")
    raw_entries, projected = cache.get(paths, work_events=snapshot.events, snapshot=snapshot)
    detail = task_detail_view(snapshot, task, raw_entries, timeline=projected)
    fingerprint = snapshot.events[-1].digest if snapshot.events else None
    revision = tracker.observe("work", fingerprint)
    payload = asdict(detail)
    children = payload.pop("children")
    events = payload.pop("events")
    communications = payload.pop("communications")
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "task": payload,
        "children": children,
        "events": events,
        "communications": communications,
    }


def _projected_timeline(
    ctx: SnapshotContext, cache: TimelineCache
) -> tuple[list[dict[str, Any]], list[TimelineEntry], tuple[Any, ...]]:
    """全量投影 + 与 snapshot 相同的 timeline 指纹，供 HTTP 与 WS delta 共用。"""
    workspace = require_workspace(ctx)
    paths = require_paths(ctx)
    snapshot = _optional_work_snapshot(workspace)
    work_events = snapshot.events if snapshot is not None else ()
    raw_entries, projected = cache.get(paths, work_events=work_events, snapshot=snapshot)
    fingerprint = (
        len(raw_entries),
        projected[-1].key if projected else "",
        projected[-1].outcome if projected else "",
    )
    return raw_entries, projected, fingerprint


def timeline_entry_payload(entry: TimelineEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "key": entry.key,
        "at": entry.at,
        "ts": entry.ts,
        "sender": entry.sender,
        "to": entry.to,
        "text": entry.text,
        "outcome": entry.outcome,
        "reason": entry.reason,
        "task_id": entry.task_id,
        "attachment_count": entry.attachment_count,
        "category": str(entry.resolved_category),
        "has_body": entry.has_body,
    }


def timeline_dto(
    ctx: SnapshotContext,
    tracker: RevisionTracker,
    cache: TimelineCache,
    *,
    category: str = "all",
    limit: int = HISTORY_LIMIT,
    before_seq: int | None = None,
) -> dict[str, Any]:
    raw_entries, projected, fingerprint = _projected_timeline(ctx, cache)
    revision = tracker.observe("timeline", fingerprint)

    counts: dict[str, int] = {"all": len(projected)}
    counts.update({str(item): 0 for item in TimelineCategory})
    for entry in projected:
        counts[str(entry.resolved_category)] += 1

    candidates = projected
    if category != "all":
        try:
            wanted = TimelineCategory(category)
        except ValueError as exc:
            raise ApiError(
                "invalid-request",
                f"未知的 category: {category!r}",
                status_code=400,
                domain="timeline",
            ) from exc
        candidates = [entry for entry in candidates if entry.resolved_category == wanted]
    if before_seq is not None:
        candidates = [entry for entry in candidates if entry.seq < before_seq]

    page = candidates[-limit:]
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "entries": [timeline_entry_payload(entry) for entry in page],
        "category_counts": counts,
        "head_seq": projected[-1].seq if projected else 0,
        "oldest_seq": page[0].seq if page else None,
        "has_more": len(candidates) > len(page),
    }


def timeline_body_text(ctx: SnapshotContext, cache: TimelineCache, seq: int) -> str:
    """§2.3 取证端点：返回未清洗原文；调用方(路由层)负责 text/plain + attachment 头。"""
    workspace = require_workspace(ctx)
    paths = require_paths(ctx)
    snapshot = _optional_work_snapshot(workspace)
    work_events = snapshot.events if snapshot is not None else ()
    raw_entries, projected = cache.get(paths, work_events=work_events, snapshot=snapshot)
    key_by_seq = {entry.seq: entry.key for entry in projected}
    key = key_by_seq.get(seq)
    if key is None:
        raise ApiError("not-found", f"时间线记录 {seq} 不存在", status_code=404, domain="timeline")
    audit = AuditLog(paths)
    for index, raw in enumerate(raw_entries):
        if from_audit(raw, index=index).key != key:
            continue
        body = audit.read_body(raw)
        if body is not None:
            return body
    raise ApiError("not-found", f"时间线记录 {seq} 没有全文", status_code=404, domain="timeline")


def members_dto(
    ctx: SnapshotContext,
    tracker: RevisionTracker,
    member_status: MemberStatusService,
) -> dict[str, Any]:
    """`member_status` 是常驻服务(`web.app` 的 lifespan 里起，后台真正 watch 输出)；

    这里绝不能每请求新建一个——一个从没被 `_watch_member` 喂过样本的
    `ActivityTracker` 永远落在 `idle`(`tmuxctl/activity.py:69,115-120`)，
    `working`/`stuck` 不可达、`silent_for` 恒为 `None`(评审 opus 实测发现)。
    `track()` 让常驻服务的成员集合跟上名册变化(新增/移除)，不重启监视任务；
    没有 tmux 就没有后台监视任务纠正 `alive`，`track()` 新增的成员一样要
    显式 `set_alive(False)`(否则默认 `True`，对齐 `console/app.py:303-305`)。
    """
    require_workspace(ctx)
    paths = require_paths(ctx)
    member_status.track(ctx.names)
    if member_status.tmux is None:
        for name in ctx.names:
            member_status.set_alive(name, False)
    view = member_status.snapshot_view(queued=pending_counts(paths))
    members = [asdict(member) for member in view.members]
    # silent_for 随墙钟漂移，前端用 snapshot_at 自行校正(§4.10)；放进指纹会
    # 让 0.5s 轮询每次都 bump，变成空推送。
    fingerprint = tuple(
        (item["name"], item["state"], item["queued"], item["alive"], item["source"])
        for item in members
    )
    revision = tracker.observe("member", fingerprint)
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "snapshot_at": view.snapshot_at,
        "members": members,
    }


def health_dto(
    ctx: SnapshotContext,
    tracker: RevisionTracker,
    monitor: HealthMonitor | None = None,
) -> dict[str, Any]:
    require_workspace(ctx)
    paths = require_paths(ctx)
    # 有常驻监视器时也只 probe、不 update：边沿归 EventHub，避免 HTTP 读快照
    # 把 raise/clear 吃掉导致 WS 丢 delta。
    probe = monitor if monitor is not None else HealthMonitor(paths, ctx.names, ctx.tmux)
    if monitor is not None:
        monitor.track(ctx.names)
    faults = probe.probe()
    fingerprint = tuple(sorted(faults.keys()))
    revision = tracker.observe("health", fingerprint)
    return {
        "epoch": tracker.epoch,
        "revision": revision,
        "degraded": bool(faults),
        "faults": [
            {
                "key": fault.key,
                "kind": str(fault.kind),
                "target": fault.target,
                "detail": fault.detail,
            }
            for fault in faults.values()
        ],
    }


def session_dto(tracker: RevisionTracker) -> dict[str, Any]:
    return {
        "actor": "human",
        "epoch": tracker.epoch,
        "epoch_started_at": tracker.epoch_started_at,
        "server_time_at": time.time(),
        "revisions": tracker.revisions(),
        "capabilities": {"stream": True, "mirror": False, "compose": False, "control": False},
    }


def bootstrap_dto(
    ctx: SnapshotContext,
    tracker: RevisionTracker,
    cache: TimelineCache,
    member_status: MemberStatusService,
    health_monitor: HealthMonitor | None = None,
) -> dict[str, Any]:
    payload = {
        "workspace": workspace_dto(ctx, tracker),
        "team": team_dto(ctx, tracker),
        "work": work_dto(ctx, tracker),
        "members": members_dto(ctx, tracker, member_status),
        "health": health_dto(ctx, tracker, health_monitor),
        "timeline": timeline_dto(ctx, tracker, cache),
        "session": session_dto(tracker),
    }
    payload["epoch"] = tracker.epoch
    payload["revisions"] = tracker.revisions()
    return payload
