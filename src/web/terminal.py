"""WEB-007 后端半边:镜像 WebSocket 通道 + 租约串行直连输入。

按 `docs/web/terminal-protocol.md` §2-§7 实现。只调 `control`(`MemberLeaseManager`、
`terminal_input_rows`)与更底层的 `tmuxctl`/`bus`(`AuditLog`)，不 import `console`——
`console.control.MemberController` 的 `press_key`/`submit_live_text` 是 TUI 专属,
这里针对同一份 `tmuxctl` 原语重新实现等价逻辑,不导入它。

完整接管(§8, WEB-008)与前端 xterm.js 接入(§10, WEB-005)不在本文件范围内。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from rich.text import Text

from control.lease import DEFAULT_TTL_SECONDS, LeaseDenied, MemberLeaseManager
from control.terminal import terminal_input_rows
from tmuxctl import KeyInjector
from tmuxctl.errors import TmuxCommandError

#: §4.2:Web 侧 10Hz 上限(实时)；回滚区内容不变，降到 2Hz 足够(§4.3)。
MIRROR_MIN_INTERVAL = 0.1
ROLLBACK_MIN_INTERVAL = 0.5
#: §4.2:静止画面每 5s 发一次 idle 帧，证明连接仍活。
IDLE_PING_INTERVAL = 5.0
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
    def capture_with_cursor(self, target: str, *, escape: bool = False) -> tuple[str, int]: ...
    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None: ...
    def has_session(self, target: str) -> bool: ...
    def fit_window(self, target: str, width: int, height: int) -> None: ...
    def release_window_size(self, target: str) -> None: ...


def _push_latest(queue: asyncio.Queue[dict[str, Any]], item: dict[str, Any]) -> None:
    """§4.4:每订阅者一个 `maxsize=1` 队列，新帧到达先清空再放入，不阻塞采集循环。"""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(item)


@dataclass
class MirrorGroup:
    """`(member, history_offset)` 一组共享一次采集(§4.3)。"""

    member: str
    history_offset: int
    tmux: MirrorTmux
    subscribers: dict[str, asyncio.Queue[dict[str, Any]]] = field(default_factory=dict)
    frame_seq: int = 0
    last_text: str | None = None
    last_broadcast_at: float = 0.0
    task: asyncio.Task[None] | None = None

    @property
    def min_interval(self) -> float:
        return MIRROR_MIN_INTERVAL if self.history_offset == 0 else ROLLBACK_MIN_INTERVAL

    def add(self, conn_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self.subscribers[conn_id] = queue
        if self.task is None:
            self.task = asyncio.create_task(self._run())
        return queue

    def remove(self, conn_id: str) -> bool:
        """返回组内是否已空(调用方据此从 `MirrorHub` 摘除)。"""
        self.subscribers.pop(conn_id, None)
        if not self.subscribers and self.task is not None:
            self.task.cancel()
            self.task = None
        return not self.subscribers

    def _broadcast(self, frame: dict[str, Any]) -> None:
        for queue in self.subscribers.values():
            _push_latest(queue, frame)

    def _capture(self) -> tuple[str, int]:
        """同步、阻塞;调用方经 `asyncio.to_thread` 跑。"""
        if self.history_offset == 0:
            return self.tmux.capture_with_cursor(self.member, escape=True)
        text = self.tmux.capture_pane(self.member, escape=True, start=-self.history_offset)
        return text, 0

    async def _run(self) -> None:
        """§4.2/§4.3:单生产者循环，无观看者时由 `remove()` 取消。"""
        try:
            while True:
                try:
                    text, cursor_y = await asyncio.to_thread(self._capture)
                except TmuxCommandError:
                    await asyncio.sleep(self.min_interval)
                    continue
                now = time.monotonic()
                if text != self.last_text:
                    self.last_text = text
                    self.frame_seq += 1
                    self.last_broadcast_at = now
                    live_allowed = self.history_offset == 0
                    input_rows: tuple[int, ...] = ()
                    if live_allowed:
                        input_rows = terminal_input_rows(strip_ansi(text))
                    self._broadcast(
                        {
                            "type": "frame",
                            "member": self.member,
                            "frame_seq": self.frame_seq,
                            "history_offset": self.history_offset,
                            "captured_at": now,
                            "cursor_y": cursor_y,
                            "input_rows": list(input_rows),
                            "live_allowed": live_allowed,
                            "encoding": "ansi",
                            "data": "[H[J" + text,
                        }
                    )
                elif now - self.last_broadcast_at >= IDLE_PING_INTERVAL:
                    self.last_broadcast_at = now
                    self._broadcast(
                        {"type": "idle", "member": self.member, "frame_seq": self.frame_seq}
                    )
                await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            pass


class MirrorHub:
    """进程内单例，挂在 `app.state`；管理全部 `(member, history_offset)` 组。"""

    def __init__(self) -> None:
        self._groups: dict[tuple[str, int], MirrorGroup] = {}

    def subscribe(
        self, member: str, history_offset: int, conn_id: str, tmux: MirrorTmux
    ) -> tuple[MirrorGroup, asyncio.Queue[dict[str, Any]]]:
        key = (member, history_offset)
        group = self._groups.get(key)
        if group is None:
            group = MirrorGroup(member=member, history_offset=history_offset, tmux=tmux)
            self._groups[key] = group
        return group, group.add(conn_id)

    def unsubscribe(self, member: str, history_offset: int, conn_id: str) -> None:
        key = (member, history_offset)
        group = self._groups.get(key)
        if group is None:
            return
        if group.remove(conn_id):
            self._groups.pop(key, None)


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

    group, queue = hub.subscribe(member, state.history_offset, owner, state.tmux)
    #: 本连接"自认为持有租约"；`lease_manager.holds()` 在被抢占的那一刻就已
    #: 变 False(文件已经是新主人)，heartbeat_loop 不能拿它当"还要不要心跳"
    #: 的判据，否则永远发现不了自己被抢——直接 heartbeat() 看返回值才对
    #: (`LeaseState`/`LeaseDenied` 语义见 control/lease.py)。
    has_lease = False

    async def forward_loop(q: asyncio.Queue[dict[str, Any]]) -> None:
        with contextlib.suppress(Exception):
            while True:
                frame = await q.get()
                await websocket.send_json(frame)

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
        group, new_queue = hub.subscribe(member, new_offset, owner, state.tmux)
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
        forward_task = asyncio.create_task(forward_loop(new_queue))

    async def handle_lease(raw: dict[str, Any]) -> None:
        nonlocal has_lease
        if raw.get("action") != "acquire":
            return
        force = bool(raw.get("force", False))
        try:
            leased = await asyncio.to_thread(lease_manager.acquire, member, owner, force=force)
        except LeaseDenied as exc:
            await websocket.send_json(
                {"type": "lease_denied", "holder": _lease_payload(exc.holder)}
            )
            return
        has_lease = True
        await websocket.send_json({"type": "lease_acquired", "holder": _lease_payload(leased)})

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
        if not lease_manager.holds(member, owner) or state.history_offset != 0:
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
        while True:
            timeout = LIVE_INPUT_MERGE_WINDOW if pending_text else None
            try:
                raw = await asyncio.wait_for(websocket.receive_json(), timeout=timeout)
            except TimeoutError:
                await flush_text(pending_text)
                continue

            msg_type = raw.get("type")
            if msg_type == "input" and raw.get("kind") == "text":
                pending_text.append(str(raw.get("data", "")))
                continue

            await flush_text(pending_text)

            if msg_type == "lease":
                await handle_lease(raw)
            elif msg_type == "scroll":
                await handle_scroll(raw)
            elif msg_type == "resize":
                await handle_resize(raw)
            elif msg_type == "focus_input":
                await handle_focus_input(raw)
            elif msg_type == "input" and raw.get("kind") == "key":
                await handle_key(raw)
            elif msg_type == "input" and raw.get("kind") == "submit":
                await handle_submit()

    try:
        await reader_loop()
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
