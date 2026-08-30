"""文件租约原语:单持有者、pid+心跳存活判定、崩溃自动可回收、显式抢占。

WEB-002 的核心约束是"两个 TUI/Web 前端并列时不得重复投递、互相 resize 或
交错键入"。这里只解决通用的互斥原语,具体接哪个动作(Hub 投递、成员 resize/
直连/接管)由上层薄封装决定,不在这里判断领域语义。

落盘格式是一个小 JSON 文件,`fcntl.flock` 保证读改写不撞车;判断"持有者是
否还活着"看两件事:心跳是否在 `ttl` 内、进程是否还在(`os.kill(pid, 0)`)。
两者有一个说明持有者已经不在了,租约自动可被下一个 `acquire()` 回收,不需要
人工介入。`force=True` 用于显式抢占——即使当前持有者仍存活也直接覆盖,对应
产品里"抢占/接管"这类主动动作。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workspace.model import Workspace

#: 心跳超过这么久没续期就当持有者已经不在了
DEFAULT_TTL_SECONDS = 15.0

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")


class LeaseDenied(Exception):
    """租约被别的存活持有者占用,且调用方没有要求强制抢占。"""

    def __init__(self, holder: LeaseState) -> None:
        self.holder = holder
        super().__init__(f"租约被 {holder.owner} 持有(pid={holder.pid}@{holder.host})")


@dataclass(frozen=True)
class LeaseState:
    """落盘的租约快照。"""

    owner: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "pid": self.pid,
            "host": self.host,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> LeaseState:
        return cls(
            owner=str(raw["owner"]),
            pid=int(raw["pid"]),  # type: ignore[arg-type]
            host=str(raw["host"]),
            acquired_at=float(raw["acquired_at"]),  # type: ignore[arg-type]
            heartbeat_at=float(raw["heartbeat_at"]),  # type: ignore[arg-type]
        )


def _pid_alive(pid: int) -> bool:
    """本机判定:能不能给这个 pid 发空信号(不真的杀,只探测存在)。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但不属于当前用户,仍算活着
    except OSError:
        return False
    return True


class Lease:
    """一把落盘的单持有者租约。

    `owner` 是调用方自选的持有者身份(例如某个前端进程或某次浏览器会话的
    唯一标识),不强行绑定进程:同一进程内可以用不同 `owner` 管理多把互不
    相干的租约(每成员一把交互租约就是这样)。`pid` 只用于崩溃探测——同一
    `owner` 换了新进程重新 `acquire()` 时,旧 pid 已经不在,视为可回收。
    """

    def __init__(self, path: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self.path = path
        self.ttl = ttl
        self._lock_path = path.with_name(path.name + ".lock")

    def _read(self) -> LeaseState | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
        try:
            return LeaseState.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None

    def _write(self, state: LeaseState | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if state is None:
            self.path.unlink(missing_ok=True)
            return
        tmp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _reclaimable(self, state: LeaseState, *, now: float) -> bool:
        if now - state.heartbeat_at > self.ttl:
            return True
        return not _pid_alive(state.pid)

    def current(self) -> LeaseState | None:
        """只读快照,不参与互斥判断——给"观察者"看当前持有者是谁用。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return self._read()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def acquire(
        self,
        owner: str,
        *,
        pid: int | None = None,
        force: bool = False,
        now: float | None = None,
    ) -> LeaseState:
        """拿到租约,或者给自己已持有的租约续期。

        非持有者在租约仍存活(心跳未超时且进程还在)时默认拿不到,抛
        `LeaseDenied`;`force=True` 无条件覆盖当前持有者,对应产品里的
        "显式抢占"(不是自动发生的,调用方必须自己决定要不要抢)。
        """
        resolved_pid = os.getpid() if pid is None else pid
        resolved_now = time.time() if now is None else now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current = self._read()
                mine = current is not None and current.owner == owner
                if (
                    current is not None
                    and not mine
                    and not force
                    and not self._reclaimable(current, now=resolved_now)
                ):
                    raise LeaseDenied(current)
                acquired_at = current.acquired_at if mine else resolved_now
                state = LeaseState(
                    owner=owner,
                    pid=resolved_pid,
                    host=socket.gethostname(),
                    acquired_at=acquired_at,
                    heartbeat_at=resolved_now,
                )
                self._write(state)
                return state
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def heartbeat(self, owner: str, *, now: float | None = None) -> bool:
        """续期;租约已经不是自己的就返回 `False`,不抛异常。"""
        try:
            self.acquire(owner, now=now)
        except LeaseDenied:
            return False
        return True

    def release(self, owner: str) -> None:
        """主动放弃;调用方不是当前持有者时是 no-op(幂等,方便退出路径统一调用)。"""
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current = self._read()
                if current is not None and current.owner == owner:
                    self._write(None)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def holds(self, owner: str) -> bool:
        state = self.current()
        return state is not None and state.owner == owner


class HubDeliveryLease:
    """每工作区单一 Hub 投递租约:接入 `bus.Hub`/`console.buspump.BusPump`。

    `should_deliver()` 就是要传给 `Hub(lease_gate=...)` 的回调:每一轮投递
    前调用一次,拿到租约(或续期)就返回 `True` 照常投递,拿不到就返回
    `False`——本轮只观察,不出队、不注入按键。持锁者退出时调用 `release()`
    主动放弃,下一个观察者立刻能接手;就算不调用(异常退出/被杀),ttl 过期
    或 pid 消失后也会被自动回收,不需要人工干预。
    """

    def __init__(self, path: Path, owner: str, *, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self._lease = Lease(path, ttl=ttl)
        self.owner = owner
        self._held = False

    @property
    def held(self) -> bool:
        """上一次 `should_deliver()` 是否拿到了租约(不会主动重新探测)。"""
        return self._held

    def should_deliver(self) -> bool:
        try:
            self._lease.acquire(self.owner)
        except LeaseDenied:
            self._held = False
            return False
        self._held = True
        return True

    def holder(self) -> LeaseState | None:
        """只读:当前谁在投递(不影响自己的持有状态)。"""
        return self._lease.current()

    def release(self) -> None:
        self._lease.release(self.owner)
        self._held = False


class MemberLeaseManager:
    """每成员一把交互租约,resize/直连键入/完整接管共用同一把互斥锁。

    多个前端可以同时"看"一个成员(镜像只读),但只有租约持有者能改尺寸、
    键入或接管;换手要么持有者主动 `release()`,要么另一方 `force=True`
    显式抢占,要么持有者所在进程崩溃/断线后按 ttl 自动回收。
    """

    def __init__(self, root: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self.root = root
        self.ttl = ttl
        self._leases: dict[str, Lease] = {}

    def _lease(self, member: str) -> Lease:
        lease = self._leases.get(member)
        if lease is None:
            filename = _UNSAFE_FILENAME.sub("_", member) or "_"
            lease = Lease(self.root / f"member-{filename}.json", ttl=self.ttl)
            self._leases[member] = lease
        return lease

    def acquire(self, member: str, owner: str, *, force: bool = False) -> LeaseState:
        return self._lease(member).acquire(owner, force=force)

    def heartbeat(self, member: str, owner: str) -> bool:
        return self._lease(member).heartbeat(owner)

    def release(self, member: str, owner: str) -> None:
        self._lease(member).release(owner)

    def holder(self, member: str) -> LeaseState | None:
        return self._lease(member).current()

    def holds(self, member: str, owner: str) -> bool:
        return self._lease(member).holds(owner)


def leases_root(workspace: Workspace) -> Path:
    """租约文件的落盘位置约定:`<state_dir>/control/leases/`。

    独立于 `bus/`(队列扫描不会误把租约文件当消息)也独立于 `work/`(账本),
    与 WEB-001 下沉的控制面共用同一个 `<state_dir>/control` 前缀。
    """
    return workspace.state_dir / "control" / "leases"
