"""网关安全:谁能用、消息怎么标记、危险指令怎么办。

三条规则,依次收紧:

1. **白名单**:只服务配置里的房间和用户。口令只证明"知道口令",不证明
   "该被服务";口令会被转发、被截图,白名单才是名单。没配白名单就一律
   拒绝,并明说去哪儿加——默认拒绝好过默认放行。
2. **来源标记**:来自 IM 的发件人一律 `im:` 前缀(GATE-001 已落),因此
   它不是 `human`,清洗(BUS-005)和限频(BUS-003)对它照常生效——远程
   身份在总线眼里就是一个普通 AI 发件人,没有任何特权。
3. **危险指令降权**:远程消息里出现 push / 删文件 / 装软件 / 出仓库这类
   要求时,**不直接入队**,先落一条待确认记录,由**本机**的人确认后才转发。
   群规里"这些事只认 human 直接指令"说的是**本机**的 human;手机上的人
   哪怕自称 human,也隔着一层网络,不该等价。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from bus.message import Message
from bus.paths import BusPaths

#: 危险指令的识别规则:(标签, 正则)。宁可多拦几条,人点一下就放行
DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("推送代码", re.compile(r"\bgit\s+push\b|推送到远端|push\s+到", re.I)),
    (
        "删除文件",
        re.compile(r"\brm\s+-[rf]|\bgit\s+clean\b|删掉文件|删除文件|删库", re.I),
    ),
    (
        "安装软件",
        re.compile(
            r"\b(brew|apt|apt-get|yum|pip|pipx|npm|pnpm|uv)\s+(install|add)\b|安装软件",
            re.I,
        ),
    ),
    (
        "访问仓库外路径",
        re.compile(r"(?<![\w./])(/etc|/usr|/System|~/\.ssh|/Users/[^/\s]+/(?!Downloads))"),
    ),
    ("改权限或凭证", re.compile(r"\bchmod\s+777\b|\bsudo\b|私钥|api[_\s-]?key|token 贴", re.I)),
)


@dataclass(frozen=True)
class SecurityPolicy:
    """谁能用这个网关。"""

    rooms: frozenset[str] = frozenset()
    users: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, rooms: object, users: object, default_room: str) -> SecurityPolicy:
        room_set = {str(item) for item in rooms} if isinstance(rooms, (list, tuple, set)) else set()
        user_set = {str(item) for item in users} if isinstance(users, (list, tuple, set)) else set()
        return cls(frozenset(room_set or {default_room}), frozenset(user_set))

    def refusal(self, room: str, user: str) -> str:
        """不放行时的说明;放行返回空串。"""
        if not self.users:
            return (
                "网关还没配白名单,谁都服务不了。"
                "在 gateway.toml 里写 users = [\"你的名字\"](或设 GATEWAY_USERS)后重启"
            )
        if room not in self.rooms:
            return f"房间 {room} 不在白名单里"
        if user not in self.users:
            return f"{user} 不在白名单里,让本机的人把你加进 gateway.toml 的 users"
        return ""

    def allows(self, room: str, user: str) -> bool:
        return not self.refusal(room, user)


def danger_in(text: str) -> str:
    """这条消息里有没有危险指令;有就返回标签,没有返回空串。"""
    for label, pattern in DANGEROUS_PATTERNS:
        if pattern.search(text):
            return label
    return ""


@dataclass
class PendingStore:
    """待本机确认的远程指令。

    落盘而不是放内存:确认命令是另一个进程(人在本机敲的),两边靠
    `bus_root/gateway/pending/` 这个目录交换状态。
    """

    paths: BusPaths
    _seen: set[str] = field(default_factory=set, init=False)

    @property
    def directory(self) -> Path:
        return self.paths.root / "gateway" / "pending"

    def add(self, message: Message, *, label: str, user: str, room: str) -> str:
        """记一条待确认;返回给人看的短 id。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        request_id = uuid.uuid4().hex[:8]
        payload = {
            "id": request_id,
            "label": label,
            "user": user,
            "room": room,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message": message.to_dict(),
        }
        path = self.directory / f"{request_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return request_id

    def entries(self) -> list[dict[str, object]]:
        if not self.directory.is_dir():
            return []
        items = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return items

    def approve(self, request_id: str) -> bool:
        """本机确认放行。写一个标记文件,跑着的网关下一轮会捡走。"""
        target = self.directory / f"{request_id}.json"
        if not target.is_file():
            return False
        target.with_suffix(".approved").write_text(
            time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8"
        )
        return True

    def reject(self, request_id: str) -> bool:
        target = self.directory / f"{request_id}.json"
        if not target.is_file():
            return False
        target.unlink()
        target.with_suffix(".approved").unlink(missing_ok=True)
        return True

    def take_approved(self) -> list[tuple[str, Message]]:
        """取走已确认的请求(取一次就消失),返回 (标签, 消息)。"""
        taken: list[tuple[str, Message]] = []
        if not self.directory.is_dir():
            return taken
        for marker in sorted(self.directory.glob("*.approved")):
            request = marker.with_suffix(".json")
            if not request.is_file():
                marker.unlink(missing_ok=True)
                continue
            payload = json.loads(request.read_text(encoding="utf-8"))
            taken.append((str(payload.get("label", "")), Message.from_dict(payload["message"])))
            request.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
        return taken
