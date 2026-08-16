"""IM 房间 → 工作区总线。

一条网关进程可以服务多个工作区:群消息上的 `room`(或显式 workspace)
对上已登记 slug,就投进那个工作区自己的总线,白名单也按区取。
没对上任何工作区时回落到网关启动时的那条总线(单工作区 / 测试)。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from bus.paths import BusPaths
from gateway.config import GatewayConfig
from gateway.security import SecurityPolicy
from roster.load import load_effective_roster
from roster.schema import RosterError
from workspace.model import Workspace
from workspace.store import Store


@dataclass(frozen=True)
class Binding:
    """一次入站对应的工作区、总线、白名单和成员名册。"""

    slug: str | None
    paths: BusPaths
    security: SecurityPolicy
    members: tuple[str, ...]
    workspace: Workspace | None = None


def members_of(workspace: Workspace | None) -> tuple[str, ...]:
    """该工作区启用的成员;读不出名册就空,由路由去提示。"""
    cwd = None if workspace is None else workspace.project_root
    try:
        roster = load_effective_roster(cwd=cwd)
    except (RosterError, OSError):
        return ()
    return tuple(member.name for member in roster.enabled_members())


class WorkspaceBinder:
    """按房间名把 IM 流量绑到对应工作区。"""

    def __init__(
        self,
        *,
        store: Store,
        config: GatewayConfig,
        fallback: BusPaths,
        fallback_security: SecurityPolicy,
        fallback_members: Callable[[], Sequence[str]],
    ) -> None:
        self.store = store
        self.config = config
        self.fallback = fallback
        self.fallback_security = fallback_security
        self.fallback_members = fallback_members

    def resolve(self, room: str) -> Binding:
        """`room` 优先当 slug;否则看哪个工作区的 rooms 名单收了这个房间。"""
        workspace = self.store.get(room)
        if workspace is None:
            workspace = self._by_configured_room(room)
        if workspace is None:
            return Binding(
                slug=self.fallback.workspace,
                paths=self.fallback,
                security=self.fallback_security,
                members=tuple(self.fallback_members()),
            )
        return Binding(
            slug=workspace.slug,
            paths=BusPaths.for_workspace(workspace).ensure(),
            security=self.policy_for(workspace.slug),
            members=members_of(workspace),
            workspace=workspace,
        )

    def _by_configured_room(self, room: str) -> Workspace | None:
        for spec in self.config.workspaces:
            rooms = spec.rooms or (spec.slug,)
            if room in rooms:
                return self.store.get(spec.slug)
        return None

    def policy_for(self, slug: str) -> SecurityPolicy:
        spec = self.config.workspace_spec(slug)
        if spec is None:
            rooms = self.fallback_security.rooms | {slug}
            return SecurityPolicy(rooms, self.fallback_security.users)
        rooms = spec.rooms or (slug,)
        users = spec.users or tuple(self.fallback_security.users)
        return SecurityPolicy(frozenset(rooms), frozenset(users))

    def iter_paths(self) -> list[BusPaths]:
        """这条网关要扫的全部总线:回落根 + 每个已登记工作区。"""
        seen: dict[str, BusPaths] = {str(self.fallback.root): self.fallback}
        for workspace in self.store.list():
            paths = BusPaths.for_workspace(workspace).ensure()
            seen[str(paths.root)] = paths
        return list(seen.values())
