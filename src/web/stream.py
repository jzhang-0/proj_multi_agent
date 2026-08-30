"""WEB-004 versioned 实时事件流(docs/web/api-protocol.md §5)。

一条 `/api/v1/stream` 连接承载全部域。领域变更检测与入队永远非阻塞；
慢客户端按 overflow resync → 全域 resync → close 1013 降级，不积压。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from bus.hub import watchfiles_available
from control.health import FaultEvent, HealthMonitor
from control.members import MemberStatusService
from control.tasks import task_event_view
from web.auth import COOKIE_NAME, WebSession
from web.context import SnapshotContext, build_context
from web.errors import ApiError
from web.snapshots import (
    _optional_work_snapshot,
    _projected_timeline,
    members_dto,
    team_dto,
    timeline_entry_payload,
    work_dto,
    workspace_dto,
)
from web.state import DOMAINS, RevisionTracker, TimelineCache

CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_UNAVAILABLE = 4503
CLOSE_SLOW = 1013
DELTA_DOMAINS = frozenset({"timeline", "work", "health"})
RING_CAPACITIES = {"timeline": 512, "work": 512, "health": 128}
CLIENT_FRAME_TYPES = frozenset({"subscribe", "unsubscribe", "pong"})
DEFAULT_QUEUE_MAX = 256
FILE_DEBOUNCE_MS = 50
HELLO_WAIT_S = 1.0
_UNSET: object = object()


def allowed_origins(port: int) -> frozenset[str]:
    """WS Origin 白名单(§6.3)：缺头或其它来源一律拒绝升级。"""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def encode_frame(frame: dict[str, Any]) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class StreamSettings:
    """可注入的节奏；生产默认对齐协议，测试缩短以免空等。"""

    queue_max: int = DEFAULT_QUEUE_MAX
    ping_interval_s: float = 30.0
    idle_timeout_s: float = 90.0
    member_interval_s: float = 0.5
    health_interval_s: float = 0.5
    file_debounce_ms: int = FILE_DEBOUNCE_MS
    watch_poll_s: float = 0.2
    hello_wait_s: float = HELLO_WAIT_S


class DeltaRing:
    """按 revision 索引的有界重放环；缺口或超容量时 subscribe 走 resync。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._frames: dict[int, dict[str, Any]] = {}
        self._order: deque[int] = deque()

    def append(self, revision: int, frame: dict[str, Any]) -> None:
        self._frames[revision] = frame
        self._order.append(revision)
        while len(self._order) > self.capacity:
            old = self._order.popleft()
            self._frames.pop(old, None)

    def replay(self, after_revision: int, current: int) -> list[dict[str, Any]] | None:
        if after_revision >= current:
            return []
        needed = list(range(after_revision + 1, current + 1))
        if any(item not in self._frames for item in needed):
            return None
        return [self._frames[item] for item in needed]

    def clear(self) -> None:
        self._frames.clear()
        self._order.clear()


class StreamClient:
    """每连接一个有界待发队列；入队非阻塞(§5.6)。"""

    def __init__(self, *, queue_max: int) -> None:
        self.queue_max = queue_max
        self.domains: set[str] = set()
        self.last_client_at = time.monotonic()
        self.close_code: int | None = None
        self._pending: deque[dict[str, Any]] = deque()
        self._not_empty = asyncio.Event()
        self._global_overflow = False

    def touch(self) -> None:
        self.last_client_at = time.monotonic()

    def enqueue(self, frame: dict[str, Any]) -> int | None:
        """入队成功返回 None；应关闭时返回 close code。"""
        if self.close_code is not None:
            return self.close_code
        if self._global_overflow:
            self.close_code = CLOSE_SLOW
            return CLOSE_SLOW
        if self.queue_max <= 0:
            self.close_code = CLOSE_SLOW
            return CLOSE_SLOW
        if len(self._pending) < self.queue_max:
            self._pending.append(frame)
            self._not_empty.set()
            return None
        domain = str(frame.get("domain") or "*")
        kept = deque(
            item
            for item in self._pending
            if not (item.get("type") == "delta" and item.get("domain") == domain)
        )
        overflow = {
            "type": "resync",
            "epoch": frame.get("epoch"),
            "domain": domain,
            "reason": "overflow",
        }
        if len(kept) < self.queue_max:
            kept.append(overflow)
            self._pending = kept
            self._not_empty.set()
            return None
        self._pending.clear()
        self._pending.append(
            {
                "type": "resync",
                "epoch": frame.get("epoch"),
                "domain": "*",
                "reason": "overflow",
            }
        )
        self._global_overflow = True
        self._not_empty.set()
        return None

    async def get(self, *, timeout: float) -> dict[str, Any] | None:
        if self._pending:
            return self._pending.popleft()
        self._not_empty.clear()
        try:
            await asyncio.wait_for(self._not_empty.wait(), timeout=timeout)
        except TimeoutError:
            return None
        if self._pending:
            return self._pending.popleft()
        return None


def _workspace_identity(ctx: SnapshotContext) -> tuple[str, str] | None:
    if ctx.workspace is None:
        return None
    return (ctx.workspace.slug, str(ctx.workspace.project_root))


def _watch_paths(ctx: SnapshotContext) -> list[Path]:
    """只盯状态/名册/账本/审计，不监视整个项目根(避免源码改动误触发)。"""
    workspace = ctx.workspace
    if workspace is None:
        return []
    candidates: list[Path] = [workspace.state_dir]
    for name in ("amux.toml", "roster.toml"):
        path = workspace.project_root / name
        if path.exists():
            candidates.append(path)
    teams = workspace.state_dir.parent.parent / "teams"
    if teams.exists():
        candidates.append(teams)
    seen: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.append(resolved)
    return seen


def _seq_by_key(entries: Iterable[dict[str, Any]]) -> dict[str, int]:
    return {str(item["key"]): int(item["seq"]) for item in entries if item.get("key")}


def _timeline_ops(
    old: dict[int, dict[str, Any]], new_entries: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """按 key→seq 映射比对。任一已知 key 的 seq 变了就返回 None，不产 ops。

    ``history_from_entries`` 按 ``at`` 排序后 enumerate 赋 seq；秒粒度审计
    ts 让同一秒内的总线消息插到任务事件前面是常态，按 seq 槽 diff 会把旧
    行 update 成新消息、再 append 一份旧行。
    """
    old_seq = _seq_by_key(old.values())
    new_seq = _seq_by_key(new_entries)
    if any(new_seq[key] != seq for key, seq in old_seq.items() if key in new_seq):
        return None
    old_by_key = {item["key"]: item for item in old.values() if item.get("key")}
    ops: list[dict[str, Any]] = []
    for entry in new_entries:
        key = entry.get("key")
        prev = old_by_key.get(key) if key else None
        if prev is None:
            ops.append({"op": "append", "entry": entry})
            continue
        if prev.get("outcome") != entry.get("outcome") or prev.get("reason") != entry.get(
            "reason"
        ):
            ops.append(
                {
                    "op": "update",
                    "seq": entry["seq"],
                    "outcome": entry.get("outcome"),
                    "reason": entry.get("reason", ""),
                }
            )
    return ops


class EventHub:
    """监视文件与领域边沿，向已订阅连接广播 invalidation/delta。"""

    def __init__(
        self,
        *,
        tracker: RevisionTracker,
        cache: TimelineCache,
        member_status: MemberStatusService,
        health: HealthMonitor | None,
        tmux: Any,
        settings: StreamSettings,
    ) -> None:
        self.tracker = tracker
        self.cache = cache
        self.member_status = member_status
        self.health = health
        self._tmux = tmux
        self.settings = settings
        self._clients: list[StreamClient] = []
        self._rings = {name: DeltaRing(size) for name, size in RING_CAPACITIES.items()}
        self._published: dict[str, int] = {}
        self._last_timeline: dict[int, dict[str, Any]] = {}
        self._last_work_seq: int | None = None
        self._identity: tuple[str, str] | None | object = _UNSET
        self._stopped = False
        self._watch_stop = asyncio.Event()
        self._primed = asyncio.Event()
        self.timeline_gap_resyncs = 0

    def add_client(self) -> StreamClient:
        client = StreamClient(queue_max=self.settings.queue_max)
        self._clients.append(client)
        return client

    def remove_client(self, client: StreamClient) -> None:
        if client in self._clients:
            self._clients.remove(client)

    def hello(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "epoch": self.tracker.epoch,
            "epoch_started_at": self.tracker.epoch_started_at,
            "revisions": self.tracker.revisions(),
            "limits": {
                "queue": self.settings.queue_max,
                "rings": dict(RING_CAPACITIES),
                "ping_interval_s": self.settings.ping_interval_s,
                "idle_timeout_s": self.settings.idle_timeout_s,
            },
        }

    def _broadcast(self, frame: dict[str, Any], *, subscribed_only: bool = True) -> None:
        domain = frame.get("domain")
        dead: list[StreamClient] = []
        for client in tuple(self._clients):
            if subscribed_only and domain not in ("*", None) and domain not in client.domains:
                continue
            code = client.enqueue(frame)
            if code is not None:
                dead.append(client)
        for client in dead:
            self.remove_client(client)

    def _emit(self, domain: str, frame: dict[str, Any]) -> None:
        rev = int(frame["revision"])
        primed = domain in self._published
        replayable = domain in self._rings and frame.get("type") in {"delta", "resync"}
        if not primed:
            self._published[domain] = rev
            if replayable:
                self._rings[domain].append(rev, frame)
            return
        if rev <= self._published[domain]:
            return
        self._published[domain] = rev
        if replayable:
            self._rings[domain].append(rev, frame)
        self._broadcast(frame)

    def _reset_generation(self) -> None:
        self.tracker.reset_epoch()
        for ring in self._rings.values():
            ring.clear()
        self._published.clear()
        self._last_timeline.clear()
        self._last_work_seq = None
        self._broadcast(
            {"type": "epoch_changed", "epoch": self.tracker.epoch},
            subscribed_only=False,
        )

    def _scan_workspace(self, ctx: SnapshotContext) -> None:
        identity = _workspace_identity(ctx)
        if self._identity is _UNSET:
            self._identity = identity
        elif identity != self._identity:
            self._identity = identity
            self._reset_generation()
        body = workspace_dto(ctx, self.tracker)
        self._emit(
            "workspace",
            {
                "type": "invalidation",
                "epoch": self.tracker.epoch,
                "domain": "workspace",
                "revision": body["revision"],
            },
        )

    def _scan_team(self, ctx: SnapshotContext) -> None:
        try:
            body = team_dto(ctx, self.tracker)
        except ApiError:
            return
        self._emit(
            "team",
            {
                "type": "invalidation",
                "epoch": self.tracker.epoch,
                "domain": "team",
                "revision": body["revision"],
            },
        )

    def _scan_roster(self, ctx: SnapshotContext) -> None:
        revision = self.tracker.observe("roster", ctx.names)
        self._emit(
            "roster",
            {
                "type": "invalidation",
                "epoch": self.tracker.epoch,
                "domain": "roster",
                "revision": revision,
            },
        )

    def _scan_work(self, ctx: SnapshotContext) -> None:
        try:
            body = work_dto(ctx, self.tracker)
        except ApiError:
            return
        snapshot = _optional_work_snapshot(ctx.workspace) if ctx.workspace is not None else None
        events = snapshot.events if snapshot is not None else ()
        last_seq = self._last_work_seq
        new_events = [event for event in events if last_seq is not None and event.seq > last_seq]
        self._last_work_seq = events[-1].seq if events else 0
        ops: list[dict[str, Any]] = [
            {
                "op": "append",
                "event": asdict(task_event_view(event)),
                "task_id": event.task_id,
            }
            for event in new_events
        ]
        if ops:
            ops.append({"op": "invalidate", "scope": "tasks"})
        self._emit(
            "work",
            {
                "type": "delta",
                "epoch": self.tracker.epoch,
                "domain": "work",
                "revision": body["revision"],
                "ops": ops,
            },
        )

    def _scan_timeline(self, ctx: SnapshotContext) -> None:
        try:
            _raw, projected, fingerprint = _projected_timeline(ctx, self.cache)
        except ApiError:
            return
        revision = self.tracker.observe("timeline", fingerprint)
        payloads = [timeline_entry_payload(entry) for entry in projected]
        ops = _timeline_ops(self._last_timeline, payloads)
        self._last_timeline = {int(item["seq"]): item for item in payloads}
        if ops is None:
            self.timeline_gap_resyncs += 1
            self._emit(
                "timeline",
                {
                    "type": "resync",
                    "epoch": self.tracker.epoch,
                    "domain": "timeline",
                    "revision": revision,
                    "reason": "gap",
                },
            )
            return
        self._emit(
            "timeline",
            {
                "type": "delta",
                "epoch": self.tracker.epoch,
                "domain": "timeline",
                "revision": revision,
                "ops": ops,
            },
        )

    def scan_files_now(self) -> None:
        ctx = build_context(tmux=self._tmux)
        self._scan_workspace(ctx)
        self._scan_team(ctx)
        self._scan_roster(ctx)
        self._scan_work(ctx)
        self._scan_timeline(ctx)

    def scan_members_now(self) -> None:
        ctx = build_context(tmux=self._tmux)
        if ctx.workspace is None:
            return
        try:
            body = members_dto(ctx, self.tracker, self.member_status)
        except ApiError:
            return
        self._emit(
            "member",
            {
                "type": "invalidation",
                "epoch": self.tracker.epoch,
                "domain": "member",
                "revision": body["revision"],
            },
        )

    def apply_health_events(self, events: Iterable[FaultEvent]) -> None:
        if self.health is None:
            return
        event_list = list(events)
        if not event_list:
            return
        fingerprint = tuple(sorted(self.health.active))
        revision = self.tracker.observe("health", fingerprint)
        ops = []
        for event in event_list:
            fault = {
                "key": event.fault.key,
                "kind": str(event.fault.kind),
                "target": event.fault.target,
                "detail": event.fault.detail,
            }
            ops.append({"op": "clear" if event.recovered else "raise", "fault": fault})
        self._emit(
            "health",
            {
                "type": "delta",
                "epoch": self.tracker.epoch,
                "domain": "health",
                "revision": revision,
                "ops": ops,
            },
        )

    def subscribe(self, client: StreamClient, message: dict[str, Any]) -> None:
        epoch = message.get("epoch")
        if epoch != self.tracker.epoch:
            client.enqueue({"type": "epoch_changed", "epoch": self.tracker.epoch})
            return
        requested = message.get("domains") or list(DOMAINS)
        if not isinstance(requested, list):
            client.enqueue(
                {
                    "type": "error",
                    "epoch": self.tracker.epoch,
                    "code": "invalid-request",
                    "message": "domains 必须是数组",
                }
            )
            return
        client.domains = {str(item) for item in requested if item in DOMAINS}
        known = message.get("known") or {}
        if not isinstance(known, dict):
            known = {}
        current = self.tracker.revisions()
        for domain in client.domains:
            self._resync_domain(client, domain, known.get(domain), current[domain])

    def unsubscribe(self, client: StreamClient, message: dict[str, Any]) -> None:
        domains = message.get("domains") or []
        if isinstance(domains, list):
            client.domains -= {str(item) for item in domains}

    def _resync_domain(
        self,
        client: StreamClient,
        domain: str,
        known: object,
        current: int,
    ) -> None:
        if not isinstance(known, int) or known < 0:
            client.enqueue(
                {
                    "type": "resync",
                    "epoch": self.tracker.epoch,
                    "domain": domain,
                    "reason": "gap",
                }
            )
            return
        if known == current:
            return
        if domain in DELTA_DOMAINS and isinstance(known, int) and known < current:
            frames = self._rings[domain].replay(known, current)
            if frames is not None:
                for frame in frames:
                    client.enqueue(frame)
                return
        client.enqueue(
            {
                "type": "resync",
                "epoch": self.tracker.epoch,
                "domain": domain,
                "reason": "gap",
            }
        )

    async def wait_primed(self) -> None:
        """升级后尽快发 hello；首轮扫描完成或超时（§5.3 不能无限等）。"""
        try:
            await asyncio.wait_for(self._primed.wait(), timeout=self.settings.hello_wait_s)
        except TimeoutError:
            self._primed.set()

    async def scan_files(self) -> None:
        self.scan_files_now()

    async def scan_members(self) -> None:
        self.scan_members_now()

    async def run(self) -> None:
        try:
            self.scan_files_now()
            self.scan_members_now()
        finally:
            self._primed.set()
        tasks = [
            asyncio.create_task(self._watch_files(), name="web-stream-watch"),
            asyncio.create_task(self._poll_members(), name="web-stream-members"),
            asyncio.create_task(self._run_health(), name="web-stream-health"),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        finally:
            self._stopped = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stopped = True
        self._watch_stop.set()

    async def _watch_files(self) -> None:
        while not self._stopped:
            ctx = build_context(tmux=self._tmux)
            paths = _watch_paths(ctx)
            if not paths or not watchfiles_available():
                await asyncio.sleep(self.settings.watch_poll_s)
                if not self._stopped:
                    await self.scan_files()
                continue
            try:
                await self._awatch(paths)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self.settings.watch_poll_s)

    async def _awatch(self, paths: list[Path]) -> None:
        import watchfiles

        async for changes in watchfiles.awatch(
            *paths,
            debounce=self.settings.file_debounce_ms,
            step=50,
            rust_timeout=2000,
            yield_on_timeout=True,
            stop_event=self._watch_stop,
        ):
            if self._stopped:
                return
            ctx = build_context(tmux=self._tmux)
            if _watch_paths(ctx) != paths:
                return
            if changes:
                await self.scan_files()

    async def _poll_members(self) -> None:
        while not self._stopped:
            await self.scan_members()
            await asyncio.sleep(self.settings.member_interval_s)

    async def _run_health(self) -> None:
        if self.health is None:
            return
        while not self._stopped:
            ctx = build_context(tmux=self._tmux)
            self.health.track(ctx.names)
            current = await asyncio.to_thread(self.health.probe)
            events = self.health.update(current)
            if events:
                self.apply_health_events(events)
            await asyncio.sleep(self.settings.health_interval_s)


def _error_frame(epoch: str, code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "epoch": epoch, "code": code, "message": message}


async def reject_websocket(
    websocket: WebSocket, code: int = CLOSE_UNAUTHORIZED, reason: str = ""
) -> None:
    """先 accept 再 close，自定义码才能到浏览器。

    uvicorn 在握手阶段 ``close()`` 会变成 HTTP 403，浏览器只看到 1006。
    """
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


async def handle_stream(
    websocket: WebSocket,
    *,
    session: WebSession,
    port: int,
    hub: EventHub | None,
) -> None:
    host = websocket.headers.get("host", "")
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        await reject_websocket(websocket, code=CLOSE_UNAUTHORIZED, reason=f"host:{host}")
        return
    origin = websocket.headers.get("origin", "")
    if origin not in allowed_origins(port):
        await reject_websocket(websocket, code=CLOSE_UNAUTHORIZED, reason=f"origin:{origin}")
        return
    if not session.verify_cookie(websocket.cookies.get(COOKIE_NAME)):
        await reject_websocket(websocket, code=CLOSE_UNAUTHORIZED, reason="cookie")
        return
    if hub is None:
        await reject_websocket(websocket, code=CLOSE_UNAVAILABLE, reason="hub")
        return

    await websocket.accept()
    await hub.wait_primed()
    client = hub.add_client()
    await websocket.send_text(encode_frame(hub.hello()))
    sender = asyncio.create_task(_sender_loop(websocket, client, hub), name="web-stream-sender")
    try:
        while True:
            text = await websocket.receive_text()
            client.touch()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_text(
                    encode_frame(
                        _error_frame(hub.tracker.epoch, "invalid-request", "帧不是合法 JSON")
                    )
                )
                continue
            if not isinstance(message, dict):
                await websocket.send_text(
                    encode_frame(_error_frame(hub.tracker.epoch, "invalid-request", "帧必须是对象"))
                )
                continue
            if "actor" in message:
                await websocket.send_text(
                    encode_frame(
                        _error_frame(
                            hub.tracker.epoch,
                            "invalid-request",
                            "不接受客户端自报 actor",
                        )
                    )
                )
                continue
            kind = message.get("type")
            if kind not in CLIENT_FRAME_TYPES:
                await websocket.send_text(
                    encode_frame(
                        _error_frame(hub.tracker.epoch, "invalid-request", f"未知帧类型: {kind!r}")
                    )
                )
                continue
            if kind == "subscribe":
                hub.subscribe(client, message)
            elif kind == "unsubscribe":
                hub.unsubscribe(client, message)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        hub.remove_client(client)


async def _sender_loop(websocket: WebSocket, client: StreamClient, hub: EventHub) -> None:
    last_ping = time.monotonic()
    settings = hub.settings
    try:
        while True:
            if client.close_code is not None:
                await websocket.close(code=client.close_code)
                return
            now = time.monotonic()
            if now - client.last_client_at > settings.idle_timeout_s:
                await websocket.close(code=1001)
                return
            if now - last_ping >= settings.ping_interval_s:
                await websocket.send_text(
                    encode_frame({"type": "ping", "epoch": hub.tracker.epoch})
                )
                last_ping = now
            frame = await client.get(timeout=0.25)
            if frame is None:
                continue
            await websocket.send_text(encode_frame(frame))
    except WebSocketDisconnect:
        return
