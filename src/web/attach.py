"""WEB-008 完整接管 WebSocket：固定 tmux attach PTY 与统一 teardown。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from control.lease import DEFAULT_TTL_SECONDS, LeaseDenied, MemberLeaseManager
from tmuxctl import TmuxAttachProcess

#: BUG(T-025):与 web/terminal.py 同款结构化日志，覆盖完整接管通道的票据/
#: 首帧拒绝路径。
logger = logging.getLogger("web.attach")

ATTACH_HEARTBEAT_INTERVAL = DEFAULT_TTL_SECONDS / 3
ATTACH_AUTH_TIMEOUT = 5.0
MIN_ATTACH_SIZE = (20, 5)
MAX_ATTACH_SIZE = (500, 200)


@dataclass
class _ActiveAttach:
    stop: asyncio.Event
    done: asyncio.Event


class AttachRegistry:
    """同一 Web 进程内让 force 抢占先等旧 PTY 完整 teardown，再开新 PTY。"""

    def __init__(self) -> None:
        self._active: dict[str, _ActiveAttach] = {}
        self._lock = asyncio.Lock()

    async def preempt(self, member: str) -> None:
        async with self._lock:
            active = self._active.get(member)
            if active is not None:
                active.stop.set()
        if active is not None:
            await active.done.wait()

    async def register(self, member: str) -> _ActiveAttach:
        active = _ActiveAttach(asyncio.Event(), asyncio.Event())
        async with self._lock:
            self._active[member] = active
        return active

    async def finish(self, member: str, active: _ActiveAttach) -> None:
        async with self._lock:
            if self._active.get(member) is active:
                self._active.pop(member, None)
            active.done.set()


def _size(raw: dict[str, Any]) -> tuple[int, int]:
    try:
        cols, rows = int(raw.get("cols", 0)), int(raw.get("rows", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("cols/rows 必须是整数") from exc
    if not (MIN_ATTACH_SIZE[0] <= cols <= MAX_ATTACH_SIZE[0]):
        raise ValueError("cols 超出允许范围")
    if not (MIN_ATTACH_SIZE[1] <= rows <= MAX_ATTACH_SIZE[1]):
        raise ValueError("rows 超出允许范围")
    return cols, rows


async def run_attach_connection(
    websocket: Any,
    *,
    member: str,
    owner: str,
    tmux: Any,
    lease_manager: MemberLeaseManager,
    audit: Any,
    registry: AttachRegistry,
    authorize,
    window_guard: Any | None = None,
    heartbeat_interval: float = ATTACH_HEARTBEAT_INTERVAL,
    auth_timeout: float = ATTACH_AUTH_TIMEOUT,
) -> None:
    """读取首帧配置后接管；任意结束原因都落到同一个 ``finally``。"""
    process: TmuxAttachProcess | None = None
    active: _ActiveAttach | None = None
    acquired = False
    reason = "断线释放"
    started = False
    tasks: set[asyncio.Task[str]] = set()
    try:
        try:
            initial = await asyncio.wait_for(websocket.receive_json(), timeout=auth_timeout)
        except (TimeoutError, ValueError, TypeError):
            logger.warning(
                "ws_attach_reject member=%s owner=%s reason=first-frame-timeout", member, owner
            )
            await websocket.close(code=4401, reason="unauthorized")
            return
        if initial.get("type") != "attach":
            logger.warning(
                "ws_attach_reject member=%s owner=%s reason=bad-first-frame-type type=%r",
                member,
                owner,
                initial.get("type"),
            )
            await websocket.close(code=4401, reason="unauthorized")
            return
        if "actor" in initial:
            logger.warning(
                "ws_attach_reject member=%s owner=%s reason=client-reported-actor", member, owner
            )
            await websocket.close(code=4401, reason="unauthorized")
            return
        attach_token = initial.get("attach_token")
        if not isinstance(attach_token, str) or not attach_token:
            logger.warning(
                "ws_ticket_reject member=%s owner=%s reason=missing-token", member, owner
            )
            await websocket.close(code=4401, reason="unauthorized")
            return
        try:
            await asyncio.to_thread(authorize, attach_token)
        except Exception as exc:
            logger.warning(
                "ws_ticket_reject member=%s owner=%s reason=invalid-token detail=%s",
                member,
                owner,
                exc,
            )
            await websocket.close(code=4401, reason="unauthorized")
            return
        try:
            cols, rows = _size(initial)
        except ValueError as exc:
            await websocket.send_json({"type": "denied", "reason": str(exc)})
            return
        force = bool(initial.get("force", False))
        if force:
            await registry.preempt(member)
        try:
            leased = await asyncio.to_thread(
                lease_manager.acquire, member, owner, force=force
            )
        except LeaseDenied as exc:
            await websocket.send_json(
                {
                    "type": "lease_denied",
                    "holder": {
                        "owner": exc.holder.owner,
                        "host": exc.holder.host,
                        "acquired_at": exc.holder.acquired_at,
                    },
                }
            )
            return
        acquired = True
        if not await asyncio.to_thread(tmux.has_session, member):
            await websocket.send_json({"type": "denied", "reason": "session-not-found"})
            return
        active = await registry.register(member)
        await asyncio.to_thread(tmux.release_window_size, member)
        if window_guard is not None:
            window_guard.untrack(member)
        process = await asyncio.to_thread(
            TmuxAttachProcess.spawn, tmux, member, cols=cols, rows=rows
        )
        await asyncio.to_thread(
            audit.record_control,
            "takeover",
            member,
            changed=True,
            detail="web attach",
        )
        started = True
        await websocket.send_json(
            {
                "type": "attached",
                "holder": {
                    "owner": leased.owner,
                    "host": leased.host,
                    "acquired_at": leased.acquired_at,
                },
            }
        )

        async def pty_to_web() -> str:
            while True:
                chunk = process.read()
                if chunk == b"":
                    return "正常退出"
                if chunk:
                    await websocket.send_bytes(chunk)
                else:
                    await asyncio.sleep(0.01)

        async def web_to_pty() -> str:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return "断线释放"
                data = message.get("bytes")
                if data is not None:
                    process.write(data)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if item.get("type") == "exit":
                    return "主动退出"
                if item.get("type") == "resize":
                    try:
                        next_cols, next_rows = _size(item)
                    except ValueError:
                        continue
                    process.resize(next_cols, next_rows)

        async def heartbeat() -> str:
            while True:
                await asyncio.sleep(heartbeat_interval)
                ok = await asyncio.to_thread(lease_manager.heartbeat, member, owner)
                if not ok:
                    return "租约被抢占"

        async def preempted() -> str:
            assert active is not None
            await active.stop.wait()
            return "租约被抢占"

        tasks = {
            asyncio.create_task(pty_to_web()),
            asyncio.create_task(web_to_pty()),
            asyncio.create_task(heartbeat()),
            asyncio.create_task(preempted()),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        reason = next(iter(done)).result()
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        async def cleanup() -> None:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            returncode = None
            if process is not None:
                returncode = await asyncio.to_thread(process.close)
            if acquired:
                await asyncio.to_thread(lease_manager.release, member, owner)
            if started:
                detail = reason if returncode is None else f"{reason}; attach exit={returncode}"
                await asyncio.to_thread(
                    audit.record_control,
                    "takeover",
                    member,
                    changed=reason == "正常退出",
                    detail=detail,
                )
            if active is not None:
                await registry.finish(member, active)
            with contextlib.suppress(Exception):
                await websocket.close(code=1000, reason=reason)

        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise
