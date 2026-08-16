"""网关抽象:群 ↔ 总线的双向桥,与具体 IM 无关。

两个方向:

- **收群消息 → 入队 bus**:`Gateway.on_group_message`,由 adapter 在收到
  群里一条消息时调用;路由与署名规则在 `gateway.router`。
- **订阅 bus → 发回群**:`Gateway.pump_once` 读审计日志(BUS-008)里的
  新事件,翻译成群消息交给 adapter 发出去。

**单 bot 模式**:群里只有一个机器人账号。谁说的话由网关在正文里代为署名
(`claude:` 前缀),不要求每个成员一个 bot 账号——这是本卷的核心简化。

adapter 只需实现三件事(`GatewayAdapter`):起、停、把一条消息发进群。
它不认识 bus,也不认识 tmux;换 IM 平台只换 adapter。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from bus.audit import AuditLog
from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit
from gateway.router import (
    Route,
    format_for_group,
    is_gateway_recipient,
    route_group_message,
)
from gateway.security import PendingStore, SecurityPolicy, danger_in

#: 轮询审计日志的间隔(秒)。群聊不需要总线那种毫秒级,0.2 秒足够跟手
PUMP_INTERVAL = 0.2

#: 会被转发到群里的审计事件(deliver 会和 deposit 重复,不转)
FORWARDED_EVENTS = ("deposit", "rejected", "deliver-failed")


@dataclass(frozen=True)
class GroupMessage:
    """群里收到的一条消息。"""

    user: str
    text: str
    room: str = "default"


@dataclass(frozen=True)
class GroupPost:
    """要发回群里的一条消息。`author` 是代发署名的对象。"""

    author: str
    text: str
    kind: str = "message"
    room: str = "default"

    def rendered(self) -> str:
        """单 bot 模式下群里看到的那一行。"""
        return format_for_group(self.author, self.text, self.kind)


class GatewayAdapter(Protocol):
    """一个 IM 平台的接入实现。网关只要求这三件事。"""

    async def start(self, on_message: Callable[[GroupMessage], None]) -> None:
        """开始接收群消息;收到一条就调用 `on_message`。"""
        ...

    async def stop(self) -> None:
        """停止接收并释放资源。"""
        ...

    async def post(self, post: GroupPost) -> None:
        """把一条消息发进群。"""
        ...


@dataclass
class Gateway:
    """群 ↔ 总线的桥。

    `members` 是可调用对象而不是固定列表:成员可能被 `/adopt` 临时收编,
    路由要跟着变。
    """

    adapter: GatewayAdapter
    paths: BusPaths
    members: Callable[[], Sequence[str]]
    room: str = "default"
    #: 白名单(GATE-004)。默认空 users = 谁都不服务
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    audit: AuditLog = field(init=False)
    pending: PendingStore = field(init=False)
    #: 已经转发过的审计条目数,避免重复发进群
    _forwarded: int = field(default=0, init=False)
    #: 每个房间上一个 @ 过的成员:不写 @ 时默认发给它
    _last_target: dict[str, str] = field(default_factory=dict, init=False)
    #: 本机确认放行后要回群里说一声的通知
    _approved_notices: list[GroupPost] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.audit = AuditLog(self.paths)
        self.pending = PendingStore(self.paths)

    # --- 收群消息 → 入队 bus ---------------------------------------------

    def route(self, message: GroupMessage) -> Route:
        return route_group_message(
            message.user,
            message.text,
            members=tuple(self.members()),
            last_target=self._last_target.get(message.room),
        )

    def on_group_message(self, message: GroupMessage) -> Route:
        """群里来了一条消息:白名单 → 路由 → 危险指令降权 → 入队。

        路由失败或被安全策略挡下都不入队,原样回群里说清楚为什么。
        """
        refusal = self.security.refusal(message.room, message.user)
        if refusal:
            return Route(error=refusal)

        route = self.route(message)
        if route.error or route.message is None:
            return route

        label = danger_in(route.message.text)
        if label:
            return Route(error=self._hold_for_confirmation(route.message, label, message))

        deposit(route.message, self.paths)
        self._last_target[message.room] = route.message.to
        return route

    def _hold_for_confirmation(self, message: Message, label: str, origin: GroupMessage) -> str:
        """危险指令不直接入队:落一条待确认,并在本机时间线上叫人。"""
        request_id = self.pending.add(message, label=label, user=origin.user, room=origin.room)
        deposit(
            Message.create(
                "human",
                f"[网关] {origin.user}(手机)要 {message.to} 做「{label}」:{message.text} —— "
                f"确认放行请在本机跑 uv run python -m gateway approve {request_id},"
                f"不同意就 reject {request_id}",
                sender="bus",
                kind="gateway-confirm",
            ),
            self.paths,
        )
        return (
            f"这条涉及「{label}」,远程指令不能直接生效,已交本机确认(编号 {request_id});"
            "本机的人放行后我再转给成员"
        )

    def release_approved(self) -> list[Message]:
        """把本机已确认的远程指令真正入队。"""
        released = []
        for label, message in self.pending.take_approved():
            deposit(message, self.paths)
            self._last_target[self.room] = message.to
            released.append(message)
            self._approved_notices.append(
                GroupPost("bus", f"本机已确认「{label}」,已转给 {message.to}", "notice", self.room)
            )
        return released

    # --- 订阅 bus → 发回群 -----------------------------------------------

    def catch_up(self) -> None:
        """把当前日志全部标记为已转发。启动时用,免得把历史一股脑倒进群里。"""
        self._forwarded = len(self.audit.entries())

    def pending_posts(self) -> list[GroupPost]:
        """自上次以来新增的、该发进群的消息。"""
        entries = self.audit.entries()
        fresh = entries[self._forwarded :]
        self._forwarded = len(entries)
        return [post for post in map(self.post_for, fresh) if post is not None]

    def post_for(self, entry: dict[str, Any]) -> GroupPost | None:
        """一条审计事件 → 群里的一条消息;不该发的返回 None。"""
        event = str(entry.get("event", ""))
        if event not in FORWARDED_EVENTS:
            return None
        author = str(entry.get("from") or "bus")
        to = str(entry.get("to") or "")
        text = str(entry.get("preview", ""))
        if event == "deposit":
            # 群里已经能看到发言人,收件人写在正文里,和终端里的格式对齐
            return GroupPost(author, f"→ {to}: {text}", "message", self.room)
        reason = str(entry.get("reason", ""))
        label = "被拒收" if event == "rejected" else "投递失败"
        return GroupPost(author, f"→ {to}: {text} ({label}:{reason})", "notice", self.room)

    async def pump_once(self) -> int:
        """把待发的消息推给 adapter,返回条数。"""
        self.release_approved()
        posts = [*self._approved_notices, *self.pending_posts()]
        self._approved_notices.clear()
        for post in posts:
            await self.adapter.post(post)
        return len(posts)

    # --- 跑起来 -----------------------------------------------------------

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起 adapter 并持续把总线流量推进群里,直到 `stop` 被置位。"""
        self.catch_up()
        await self.adapter.start(self._handle_from_adapter)
        try:
            while stop is None or not stop.is_set():
                await self.pump_once()
                await asyncio.sleep(PUMP_INTERVAL)
        finally:
            await self.adapter.stop()

    def _handle_from_adapter(self, message: GroupMessage) -> None:
        route = self.on_group_message(message)
        if route.error:
            # 路由失败不入队,直接在群里说清楚为什么
            post = GroupPost("bus", route.error, "notice", message.room)
            asyncio.get_running_loop().create_task(self.adapter.post(post))


__all__ = [
    "FORWARDED_EVENTS",
    "PUMP_INTERVAL",
    "Gateway",
    "GatewayAdapter",
    "GroupMessage",
    "GroupPost",
    "is_gateway_recipient",
]
