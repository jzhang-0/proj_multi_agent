"""Web/TUI 可共用的成员管理、生命周期与危险动作确认控制面。"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from bus import BusPaths
from bus.audit import AuditLog
from control.actions import ControlFeedback, MemberActionController
from roster.adopt import SessionAdopter
from roster.lifecycle import Lifecycle
from roster.load import load_effective_roster, load_roster
from roster.schema import RosterError
from workspace.members import add_member, remove_member
from workspace.model import Workspace

CONFIRM_TTL_SECONDS = 30.0
DANGEROUS_ACTIONS = frozenset({"terminate", "restart", "down"})


class MemberAdminError(Exception):
    """可安全返回给本机 UI 的控制面输入/状态错误。"""


@dataclass(frozen=True)
class Confirmation:
    actor: str
    member: str
    action: str
    expires_at: float


class ConfirmationStore:
    """进程内一次性确认票据；绑定 actor/member/action，默认 30 秒过期。"""

    def __init__(
        self,
        *,
        ttl: float = CONFIRM_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        allowed_actions: frozenset[str] = DANGEROUS_ACTIONS,
    ) -> None:
        self.ttl = ttl
        self._clock = clock
        self.allowed_actions = allowed_actions
        self._items: dict[str, Confirmation] = {}
        self._lock = threading.Lock()

    def issue(self, actor: str, member: str, action: str) -> tuple[str, Confirmation]:
        if action not in self.allowed_actions:
            raise MemberAdminError(f"动作 {action!r} 不允许签发票据")
        now = self._clock()
        token = secrets.token_urlsafe(24)
        confirmation = Confirmation(actor, member, action, now + self.ttl)
        with self._lock:
            self._items[token] = confirmation
            self._purge(now)
        return token, confirmation

    def consume(self, token: str, actor: str, member: str, action: str) -> None:
        now = self._clock()
        with self._lock:
            confirmation = self._items.get(token)
            if confirmation is None:
                raise MemberAdminError("确认令牌无效或已使用")
            if confirmation.expires_at < now:
                self._items.pop(token, None)
                raise MemberAdminError("确认令牌已过期")
            if (confirmation.actor, confirmation.member, confirmation.action) != (
                actor,
                member,
                action,
            ):
                raise MemberAdminError("确认令牌与操作者、成员或动作不匹配")
            self._items.pop(token, None)

    def _purge(self, now: float) -> None:
        for token, item in tuple(self._items.items()):
            if item.expires_at < now:
                self._items.pop(token, None)


class MemberAdminController:
    """唯一编排成员动作的控制面；Web 路由只传结构化动作，不碰 tmux argv。"""

    def __init__(
        self,
        workspace: Workspace,
        tmux: Any,
        paths: BusPaths,
        *,
        confirmations: ConfirmationStore | None = None,
        muted: set[str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.tmux = tmux
        self.paths = paths
        self.audit = AuditLog(paths)
        self.confirmations = confirmations or ConfirmationStore()
        self.attach_tokens = ConfirmationStore(allowed_actions=frozenset({"attach"}))
        self.direct_tokens = ConfirmationStore(allowed_actions=frozenset({"direct"}))
        self.muted = muted if muted is not None else set()
        self._adopter = SessionAdopter(self._roster(), tmux)

    def _roster(self):
        return load_effective_roster(cwd=self.workspace.project_root)

    def _configured_names(self) -> tuple[str, ...]:
        return tuple(member.name for member in self._roster().enabled_members())

    def member_names(self) -> tuple[str, ...]:
        configured = self._configured_names()
        return configured + tuple(
            name for name in self._adopter.member_names() if name not in configured
        )

    def sources(self) -> dict[str, str]:
        configured = set(self._configured_names())
        return {
            name: "roster" if name in configured else "adopted"
            for name in self.member_names()
        }

    def _require_member(self, name: str) -> None:
        if name not in self.member_names():
            raise MemberAdminError(f"未知成员: {name}")

    def _require_configured(self, name: str) -> None:
        if name not in self._configured_names():
            raise MemberAdminError(f"成员 {name!r} 不是持久名册成员")

    def _actions(self) -> MemberActionController:
        lifecycle = Lifecycle(self._roster(), self.tmux, cwd=self.workspace.project_root)
        return MemberActionController(self.tmux, lifecycle, self.audit)

    @staticmethod
    def feedback(feedback: ControlFeedback) -> dict[str, Any]:
        return asdict(feedback)

    def confirm(self, actor: str, name: str, action: str) -> dict[str, Any]:
        self._require_member(name)
        if action in {"restart", "down"}:
            self._require_configured(name)
        token, _confirmation = self.confirmations.issue(actor, name, action)
        return {"confirm_token": token, "expires_in": self.confirmations.ttl}

    def interrupt(self, name: str) -> ControlFeedback:
        self._require_member(name)
        return self._actions().interrupt(name)

    def authorize_attach(self, actor: str, name: str) -> dict[str, Any]:
        self._require_member(name)
        token, _authorization = self.attach_tokens.issue(actor, name, "attach")
        self.audit.record_control(
            "takeover-authorize",
            name,
            changed=True,
            detail=f"actor={actor}; 一次性票据 {self.attach_tokens.ttl:g}s",
        )
        return {"attach_token": token, "expires_in": self.attach_tokens.ttl}

    def consume_attach(self, actor: str, name: str, token: str) -> None:
        self._require_member(name)
        try:
            self.attach_tokens.consume(token, actor, name, "attach")
        except MemberAdminError as exc:
            self.audit.record_control(
                "takeover-authorize", name, changed=False, detail=f"票据拒绝: {exc}"
            )
            raise

    def authorize_direct(self, actor: str, name: str) -> dict[str, Any]:
        self._require_member(name)
        token, _authorization = self.direct_tokens.issue(actor, name, "direct")
        self.audit.record_control(
            "direct-authorize",
            name,
            changed=True,
            detail=f"actor={actor}; 一次性票据 {self.direct_tokens.ttl:g}s",
        )
        return {"direct_token": token, "expires_in": self.direct_tokens.ttl}

    def consume_direct(self, actor: str, name: str, token: str) -> None:
        self._require_member(name)
        try:
            self.direct_tokens.consume(token, actor, name, "direct")
        except MemberAdminError as exc:
            self.audit.record_control(
                "direct-authorize", name, changed=False, detail=f"票据拒绝: {exc}"
            )
            raise

    def terminate(self, actor: str, name: str, token: str) -> ControlFeedback:
        self._require_member(name)
        self.confirmations.consume(token, actor, name, "terminate")
        return self._actions().terminate(name)

    def restart(self, actor: str, name: str, token: str) -> ControlFeedback:
        self._require_configured(name)
        self.confirmations.consume(token, actor, name, "restart")
        return self._actions().restart(name)

    def up(self, name: str) -> ControlFeedback:
        self._require_configured(name)
        try:
            result = Lifecycle(
                self._roster(), self.tmux, cwd=self.workspace.project_root
            ).up(name)[0]
        except Exception as exc:
            self.audit.record_control("up", name, changed=False, detail=str(exc))
            raise MemberAdminError(str(exc)) from exc
        return self._record(result.action, name, result.changed, result.detail)

    def down(self, actor: str, name: str, token: str) -> ControlFeedback:
        self._require_configured(name)
        self.confirmations.consume(token, actor, name, "down")
        try:
            result = Lifecycle(
                self._roster(), self.tmux, cwd=self.workspace.project_root
            ).down(name)[0]
        except Exception as exc:
            self.audit.record_control("down", name, changed=False, detail=str(exc))
            raise MemberAdminError(str(exc)) from exc
        return self._record(result.action, name, result.changed, result.detail)

    def adopt(self, name: str) -> dict[str, Any]:
        try:
            member = self._adopter.adopt(name)
        except Exception as exc:
            self.audit.record_control("adopt", name, changed=False, detail=str(exc))
            raise MemberAdminError(str(exc)) from exc
        self.audit.record_control(
            "adopt", name, changed=True, detail="进程级临时收编，不写入名册"
        )
        return {
            "name": member.name,
            "source": "adopted",
            "temporary": True,
            "commands": list(member.commands),
        }

    def toggle_mute(self, name: str) -> dict[str, Any]:
        self._require_member(name)
        if name in self.muted:
            self.muted.remove(name)
            muted = False
        else:
            self.muted.add(name)
            muted = True
        self.audit.record_control(
            "mute", name, changed=True, detail="已静音" if muted else "已取消静音"
        )
        return {"name": name, "muted": muted}

    def add(self, name: str) -> dict[str, Any]:
        try:
            member, created = add_member(self.workspace, name, presets=load_roster())
        except (RosterError, OSError) as exc:
            self.audit.record_control("member-add", name, changed=False, detail=str(exc))
            raise MemberAdminError(str(exc)) from exc
        self._reload_adopter()
        self.audit.record_control(
            "member-add", name, changed=created, detail="已加入" if created else "已存在"
        )
        return {"name": member.name, "created": created}

    def remove(self, name: str) -> dict[str, Any]:
        try:
            member = remove_member(self.workspace, name, presets=load_roster())
        except (RosterError, OSError) as exc:
            self.audit.record_control("member-rm", name, changed=False, detail=str(exc))
            raise MemberAdminError(str(exc)) from exc
        self.muted.discard(name)
        self._reload_adopter()
        self.audit.record_control("member-rm", name, changed=True, detail="已从名册移除")
        return {"name": member.name, "removed": True}

    def listing(self) -> dict[str, Any]:
        sources = self.sources()
        candidates = self._adopter.discover()
        return {
            "members": [
                {
                    "name": name,
                    "source": sources[name],
                    "temporary": sources[name] == "adopted",
                    "muted": name in self.muted,
                    "running": bool(self.tmux.has_session(name)),
                }
                for name in self.member_names()
            ],
            "adoptable": [
                {"name": item.name, "commands": list(item.commands)} for item in candidates
            ],
            "presets": [member.name for member in load_roster().members],
        }

    def _reload_adopter(self) -> None:
        old = tuple(member.name for member in self._adopter.adopted_members())
        self._adopter = SessionAdopter(self._roster(), self.tmux)
        configured = set(self._configured_names())
        for name in old:
            if name not in configured:
                with contextlib.suppress(RosterError):
                    self._adopter.adopt(name)

    def _record(self, action: Any, name: str, changed: bool, detail: str) -> ControlFeedback:
        feedback = ControlFeedback(str(action), name, changed, detail)
        self.audit.record_control(str(action), name, changed=changed, detail=detail)
        return feedback
