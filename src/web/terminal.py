"""WEB-007 后端半边:镜像 WebSocket 通道 + 租约串行直连输入。

按 `docs/web/terminal-protocol.md` §2-§7 实现。只调 `control`(`MemberLeaseManager`、
`terminal_input_rows`、WEB-011 下沉的 `control.mirror` 采集核心)与更底层的
`tmuxctl`/`bus`(`AuditLog`)，不 import `console`——`console.control.MemberController`
的 `press_key`/`submit_live_text` 是 TUI 专属,这里针对同一份 `tmuxctl` 原语重新实现
等价逻辑,不导入它。

采集循环/背压/停采集判据本身在 `control.mirror`(TUI 同款共用，WEB-011)；本文件
只负责 ANSI 剥离与组帧(`control` 禁 import rich，见 §6.4 结论)。

完整接管(§8, WEB-008)与前端 xterm.js 接入(§10, WEB-005)不在本文件范围内。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from rich.text import Text

from control.lease import DEFAULT_TTL_SECONDS, LeaseDenied, MemberLeaseManager
from control.mirror import IdleTick, MirrorHub, RawFrame
from control.terminal import terminal_input_rows
from tmuxctl import KeyInjector

#: BUG(T-025):镜像通道的票据/未持锁写拒绝此前不落日志，human 实机复现
#: unauthorized(4401)时无法判断是缺票据、票据被拒还是"未持租约就发写帧"
#: 触发的(§6.3 白名单只有 scroll，其余一律按写处理)。
logger = logging.getLogger("web.terminal")

#: §4.2:Web 侧 10Hz 上限(实时)；回滚区内容不变，降到 2Hz 足够(§4.3)。
MIRROR_MIN_INTERVAL = 0.1
ROLLBACK_MIN_INTERVAL = 0.5
#: §4.5:回滚上限，等于 tmux 默认回滚区大小(对齐 console.mirror.HISTORY_LIMIT)。
MAX_SCROLL_OFFSET = 2000
#: §5:太小的窗口 fit 过去 CLI 反而排不下。
MIN_FIT_SIZE = (60, 15)
#: §6.2:点击直连的帧新鲜度容忍窗口(~300ms)。
STALE_FRAME_TOLERANCE = 3
#: §7.1/7.3:直连按键白名单，逐字对齐 console.control.MemberController.press_key。
KEY_WHITELIST = frozenset({"Enter", "Tab", "BTab", "BSpace", "DC", "Up", "Down", "Left", "Right"})
#: §7.3:相邻 text 消息合并窗口，对齐 TUI `_drain_live_input()` 的 8ms。
LIVE_INPUT_MERGE_WINDOW = 0.008
#: §7.3:submit 前的等待，防止 CLI 把 Enter 吞进粘贴判定。
LIVE_SUBMIT_GAP_S = 0.01
#: §3.1:心跳周期需明显小于租约 ttl，留出网络抖动余量。
HEARTBEAT_INTERVAL = DEFAULT_TTL_SECONDS / 3


def strip_ansi(ansi_text: str) -> str:
    """与 TUI `Mirror.show_screen()` 同一条剥离路径:`Text.from_ansi(...).plain`。

    §6.4 要求这里的结果与 TUI 逐字符一致，所以不用自制正则，直接复用
    `rich`(`textual` 的既有依赖，不是 `console`)做的同一次转换。
    """
    return Text.from_ansi(ansi_text, no_wrap=True, overflow="crop").plain


class MirrorTmux(Protocol):
    """`MirrorGroup`/输入执行需要的最小 tmux 面(始终传成员短名，由 `NamespacedTmux` 翻译)。"""

    def capture_pane(
        self, target: str, *, escape: bool = False, start: int | str | None = None
    ) -> str: ...
    def capture_with_geometry(
        self, target: str, *, escape: bool = False, start: int | str | None = None
    ) -> tuple[str, int, int, int]: ...
    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None: ...
    def has_session(self, target: str) -> bool: ...
    def fit_window(self, target: str, width: int, height: int) -> None: ...
    def release_window_size(self, target: str) -> None: ...


def _mirror_min_interval(history_offset: int) -> float:
    """§4.2:Web 侧实时 10Hz 上限；回滚区内容不变，降到 2Hz 足够(§4.3)。"""
    return MIRROR_MIN_INTERVAL if history_offset == 0 else ROLLBACK_MIN_INTERVAL


@dataclass
class _AsyncTmuxCapture:
    """`control.mirror.MirrorCaptureTarget` 要的是异步接口;Web 侧 tmux 客户端
    是同步/阻塞的，这里补一层 `asyncio.to_thread` 转发,不改 `control` 那边。"""

    tmux: MirrorTmux

    async def capture_with_geometry(
        self, target: str, *, escape: bool = False, start: int | str | None = None
    ) -> tuple[str, int, int, int]:
        return await asyncio.to_thread(
            self.tmux.capture_with_geometry, target, escape=escape, start=start
        )


def _frame_message(frame: RawFrame) -> dict[str, Any]:
    """把 `control.mirror` 的原始帧组成 §4.1 协议帧:ANSI 剥离/组帧留在这一侧。"""
    live_allowed = frame.history_offset == 0
    input_rows: tuple[int, ...] = ()
    if live_allowed:
        input_rows = terminal_input_rows(strip_ansi(frame.text))
    return {
        "type": "frame",
        "member": frame.member,
        "frame_seq": frame.frame_seq,
        "cols": frame.cols,
        "rows": frame.rows,
        "history_offset": frame.history_offset,
        "captured_at": frame.captured_at,
        "cursor_y": frame.cursor_y,
        "input_rows": list(input_rows),
        "live_allowed": live_allowed,
        "encoding": "ansi",
        "data": "\x1b[H\x1b[J" + frame.text,
    }


def _idle_message(tick: IdleTick) -> dict[str, Any]:
    return {"type": "idle", "member": tick.member, "frame_seq": tick.frame_seq}


def _lease_payload(state: Any) -> dict[str, Any]:
    return {"owner": state.owner, "host": state.host, "acquired_at": state.acquired_at}


@dataclass
class ConnectionState:
    """一条镜像连接的可变状态；`scroll` 切组、`focus_input`/`input` 都读写它。"""

    member: str
    owner: str
    tmux: MirrorTmux
    lease_manager: MemberLeaseManager
    audit: Any
    hub: MirrorHub
    window_guard: Any | None = None
    authorize: Any | None = None
    history_offset: int = 0
    live_active: bool = False
    last_size: tuple[int, int] | None = None
    #: 仅供测试收窄；生产走模块常量默认值(15/3=5s)，等一个心跳周期才能验
    #: "抢占后原持有者收到 lease_lost" 太慢。
    heartbeat_interval: float = HEARTBEAT_INTERVAL


async def _record_control(
    audit: Any, action: str, target: str, *, changed: bool, detail: str = ""
) -> None:
    await asyncio.to_thread(audit.record_control, action, target, changed=changed, detail=detail)


async def run_mirror_connection(websocket: Any, state: ConnectionState) -> None:
    """连接生命周期主体:订阅帧、收发消息、心跳、断线释放(§3.1/§4/§6/§7)。

    结构上只有一条"驱动"协程(本函数自身的消息循环)，`forward_task`/
    `heartbeat_task` 是纯后台泵，不参与 `asyncio.wait` 的完成集合判定——
    这样 `scroll` 切组时可以安全地取消重建 `forward_task`，不会和外层的
    完成态检测打架。
    """
    member = state.member
    owner = state.owner
    hub = state.hub
    lease_manager = state.lease_manager

    group, queue = hub.subscribe(
        member,
        state.history_offset,
        owner,
        _AsyncTmuxCapture(state.tmux),
        min_interval=_mirror_min_interval(state.history_offset),
    )
    #: 本连接"自认为持有租约"；`lease_manager.holds()` 在被抢占的那一刻就已
    #: 变 False(文件已经是新主人)，heartbeat_loop 不能拿它当"还要不要心跳"
    #: 的判据，否则永远发现不了自己被抢——直接 heartbeat() 看返回值才对
    #: (`LeaseState`/`LeaseDenied` 语义见 control/lease.py)。
    has_lease = False

    async def forward_loop(q: asyncio.Queue[RawFrame | IdleTick]) -> None:
        with contextlib.suppress(Exception):
            while True:
                item = await q.get()
                message = (
                    _frame_message(item) if isinstance(item, RawFrame) else _idle_message(item)
                )
                await websocket.send_json(message)

    async def heartbeat_loop() -> None:
        nonlocal has_lease
        while True:
            await asyncio.sleep(state.heartbeat_interval)
            if has_lease:
                ok = await asyncio.to_thread(lease_manager.heartbeat, member, owner)
                if not ok:
                    has_lease = False
                    state.live_active = False
                    with contextlib.suppress(Exception):
                        await websocket.send_json({"type": "lease_lost"})

    forward_task = asyncio.create_task(forward_loop(queue))
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def switch_group(new_offset: int) -> None:
        nonlocal group, forward_task
        hub.unsubscribe(member, state.history_offset, owner)
        state.history_offset = new_offset
        state.live_active = False
        group, new_queue = hub.subscribe(
            member,
            new_offset,
            owner,
            _AsyncTmuxCapture(state.tmux),
            min_interval=_mirror_min_interval(new_offset),
        )
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
        forward_task = asyncio.create_task(forward_loop(new_queue))

    async def handle_lease(raw: dict[str, Any]) -> None:
        nonlocal has_lease
        if raw.get("action") != "acquire":
            return
        token = raw.get("direct_token")
        if not isinstance(token, str) or not token or state.authorize is None:
            logger.warning(
                "ws_ticket_reject member=%s owner=%s reason=missing-token", member, owner
            )
            await websocket.close(code=4401, reason="unauthorized")
            raise _UnauthorizedWrite
        try:
            await asyncio.to_thread(state.authorize, token)
        except Exception as exc:
            logger.warning(
                "ws_ticket_reject member=%s owner=%s reason=invalid-token detail=%s",
                member,
                owner,
                exc,
            )
            await websocket.close(code=4401, reason="unauthorized")
            raise _UnauthorizedWrite from None
        force = bool(raw.get("force", False))
        # 评审(opus):force 抢占成功后，抢占方也该知道"刚把谁踢下来了"，不能
        # 只有原持有者单方面收到 lease_lost。读一次当前持有者是尽力而为的
        # 快照(和后面的 acquire 之间有极小窗口)，只用于 UI 提示，不影响
        # acquire 本身的正确性。
        previous = None
        if force:
            current = await asyncio.to_thread(lease_manager.holder, member)
            if current is not None and current.owner != owner:
                previous = _lease_payload(current)
        try:
            leased = await asyncio.to_thread(lease_manager.acquire, member, owner, force=force)
        except LeaseDenied as exc:
            await websocket.send_json(
                {"type": "lease_denied", "holder": _lease_payload(exc.holder)}
            )
            return
        has_lease = True
        payload: dict[str, Any] = {"type": "lease_acquired", "holder": _lease_payload(leased)}
        if previous is not None:
            payload["preempted"] = True
            payload["previous_holder"] = previous
        await websocket.send_json(payload)

    async def handle_scroll(raw: dict[str, Any]) -> None:
        try:
            offset = int(raw.get("offset", 0))
        except (TypeError, ValueError):
            return
        offset = max(0, min(offset, MAX_SCROLL_OFFSET))
        if offset != state.history_offset:
            await switch_group(offset)

    async def handle_resize(raw: dict[str, Any]) -> None:
        if not lease_manager.holds(member, owner):
            await websocket.send_json({"type": "denied", "reason": "no-lease"})
            return
        try:
            cols, rows = int(raw.get("cols", 0)), int(raw.get("rows", 0))
        except (TypeError, ValueError):
            return
        if cols < MIN_FIT_SIZE[0] or rows < MIN_FIT_SIZE[1]:
            return
        if state.last_size == (cols, rows):
            return
        if state.window_guard is not None:
            state.window_guard.track(state.tmux, member, identity=owner)
        await asyncio.to_thread(state.tmux.fit_window, member, cols, rows)
        state.last_size = (cols, rows)

    async def handle_focus_input(raw: dict[str, Any]) -> None:
        if not lease_manager.holds(member, owner):
            await websocket.send_json({"type": "denied", "reason": "no-lease"})
            return
        if state.history_offset != 0:
            await websocket.send_json({"type": "denied", "reason": "scrolled-back"})
            return
        try:
            frame_seq, row = int(raw.get("frame_seq", -1)), int(raw.get("row", -1))
        except (TypeError, ValueError):
            return
        if group.frame_seq - frame_seq > STALE_FRAME_TOLERANCE:
            await websocket.send_json({"type": "denied", "reason": "stale-frame"})
            return
        text = await asyncio.to_thread(state.tmux.capture_pane, member, escape=True)
        if row not in terminal_input_rows(strip_ansi(text)):
            await websocket.send_json({"type": "denied", "reason": "row-not-input"})
            return
        state.live_active = True
        await websocket.send_json({"type": "live", "active": True})

    accum_text: list[str] = []

    async def handle_key(raw: dict[str, Any]) -> None:
        if not lease_manager.holds(member, owner):
            await websocket.send_json({"type": "denied", "reason": "no-lease"})
            return
        if state.history_offset != 0:
            # §4.5:回滚态拒绝一切输入。
            await websocket.send_json({"type": "denied", "reason": "scrolled-back"})
            return
        name = raw.get("name")
        if name not in KEY_WHITELIST:
            return
        await asyncio.to_thread(state.tmux.send_keys, member, name)
        await _record_control(state.audit, "key", member, changed=True, detail=str(name))

    async def flush_text(pending: list[str]) -> None:
        if not pending:
            return
        text = "".join(pending)
        pending.clear()
        if not lease_manager.holds(member, owner) or state.history_offset != 0:
            return
        await asyncio.to_thread(state.tmux.send_keys, member, text, literal=True)
        accum_text.append(text)

    async def handle_submit() -> None:
        # 评审(opus):submit 是用户敲回车的离散动作，不能像 text 片段那样静默
        # 丢弃——那等于"发送的消息凭空消失，界面毫无反馈"。明确拒绝，且
        # accum_text 原样保留(不清空):文字仍留在 tmux 输入框里(send_keys
        # 早已发过)，回到直连态后可以再次 submit 提交同一段话；这样审计里
        # `type` 记的 detail 永远是"实际提交成功的那段"，不会因为半路被拦
        # 而和真实送进 tmux 的内容错位(§7.4)。
        if not lease_manager.holds(member, owner):
            await websocket.send_json({"type": "denied", "reason": "no-lease"})
            return
        if state.history_offset != 0:
            await websocket.send_json({"type": "denied", "reason": "scrolled-back"})
            return
        await asyncio.sleep(LIVE_SUBMIT_GAP_S)
        await asyncio.to_thread(state.tmux.send_keys, member, "Enter")
        text = "".join(accum_text)
        accum_text.clear()
        outcome = await asyncio.to_thread(KeyInjector(state.tmux).ensure_submitted, member, text)
        await _record_control(state.audit, "type", member, changed=outcome.submitted, detail=text)
        if not outcome.submitted:
            await websocket.send_json({"type": "notice", "text": "提交未确认，请查看成员输入区"})

    async def reader_loop() -> None:
        pending_text: list[str] = []

        async def invalid_request(message: str) -> None:
            await websocket.send_json(
                {"type": "error", "code": "invalid-request", "message": message}
            )

        while True:
            timeout = LIVE_INPUT_MERGE_WINDOW if pending_text else None
            try:
                raw = await asyncio.wait_for(websocket.receive_json(), timeout=timeout)
            except TimeoutError:
                await flush_text(pending_text)
                continue

            msg_type = raw.get("type")
            if msg_type == "scroll":
                await flush_text(pending_text)
                await handle_scroll(raw)
                continue

            if msg_type == "lease" and raw.get("action") == "acquire":
                await flush_text(pending_text)
                await handle_lease(raw)
                continue

            # §6.3:mirror 是先只读、后提权的双模连接。客户端只读白名单只有
            # scroll；除此之外一律按写处理，未持租约就关闭，避免未来新增帧
            # 忘记分类时意外默认放行。lease acquire 自己负责消费一次性票据。
            if not has_lease:
                logger.warning(
                    "ws_write_reject member=%s owner=%s reason=no-lease msg_type=%r",
                    member,
                    owner,
                    msg_type,
                )
                await websocket.close(code=4401, reason="unauthorized")
                raise _UnauthorizedWrite

            if msg_type == "input" and raw.get("kind") == "text":
                pending_text.append(str(raw.get("data", "")))
                continue

            await flush_text(pending_text)

            if msg_type == "resize":
                await handle_resize(raw)
            elif msg_type == "focus_input":
                await handle_focus_input(raw)
            elif msg_type == "input" and raw.get("kind") == "key":
                await handle_key(raw)
            elif msg_type == "input" and raw.get("kind") == "submit":
                await handle_submit()
            elif msg_type == "input":
                await invalid_request(f"未知 input kind: {raw.get('kind')!r}")
            else:
                await invalid_request(f"未知帧类型: {msg_type!r}")

    try:
        await reader_loop()
    except _UnauthorizedWrite:
        pass
    finally:
        heartbeat_task.cancel()
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
        hub.unsubscribe(member, state.history_offset, owner)
        # §3.1:连接关闭(正常或异常)必须立即释放；release() 对非持有者是幂等空操作。
        await asyncio.to_thread(lease_manager.release, member, owner)
        if state.last_size is not None:
            await asyncio.to_thread(state.tmux.release_window_size, member)
            if state.window_guard is not None:
                state.window_guard.untrack(member, identity=owner)


class _UnauthorizedWrite(Exception):
    """已向客户端发出 4401；仅用于结束当前 mirror reader。"""
