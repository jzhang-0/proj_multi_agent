"""团队档案的严格模型与 TOML 校验。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from workspace.errors import WorkspaceError

_TEAM_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_ROLES = frozenset({"leader", "member"})
_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
_SPEEDS = frozenset({"standard", "fast"})


class TeamValidationError(WorkspaceError):
    """团队档案结构或责任边界无效。"""


@dataclass(frozen=True)
class TeamMember:
    """团队中的一个可追责角色，不等同于实际 CLI 启动参数。"""

    id: str
    role: str
    model: str
    effort: str
    speed: str
    responsibility: str


@dataclass(frozen=True)
class Team:
    """一个具名团队，恰有一位 Leader。"""

    id: str
    name: str
    leader: str
    members: tuple[TeamMember, ...]
    description: str = ""

    @property
    def leader_member(self) -> TeamMember:
        return next(member for member in self.members if member.id == self.leader)


def validate_team_id(value: str) -> str:
    """校验可安全作为 ``teams/<id>.toml`` 文件名的团队 ID。"""
    if not isinstance(value, str) or not _TEAM_ID.fullmatch(value):
        raise TeamValidationError("团队 ID 必须是小写字母开头的字母、数字或连字符")
    return value


def team_from_dict(raw: Any, *, source: str) -> Team:
    """从 TOML 解析结果构建并校验团队。"""
    if not isinstance(raw, dict):
        raise TeamValidationError(f"{source} 根必须是表")
    allowed = {"id", "name", "leader", "description", "members"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise TeamValidationError(f"{source} 含未知字段: {', '.join(unknown)}")

    team_id = validate_team_id(_string(raw, "id", source))
    name = _string(raw, "name", source)
    leader = _string(raw, "leader", source)
    description = _optional_string(raw, "description", source)
    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or not members_raw:
        raise TeamValidationError(f"{source} 的 members 必须是非空数组")
    members = tuple(_member(item, source=source) for item in members_raw)
    ids = [member.id for member in members]
    duplicates = sorted({member_id for member_id in ids if ids.count(member_id) > 1})
    if duplicates:
        raise TeamValidationError(f"{source} 的成员 ID 重复: {', '.join(duplicates)}")
    leaders = [member for member in members if member.role == "leader"]
    if len(leaders) != 1:
        raise TeamValidationError(f"{source} 必须恰有一名 role = leader 的成员")
    if leader != leaders[0].id:
        raise TeamValidationError(
            f"{source} 的 leader 必须指向唯一 Leader {leaders[0].id!r}"
        )
    if not any(member.role == "member" for member in members):
        raise TeamValidationError(f"{source} 至少需要一名 role = member 的成员")
    return Team(
        id=team_id,
        name=name,
        leader=leader,
        members=members,
        description=description,
    )


def _member(raw: Any, *, source: str) -> TeamMember:
    if not isinstance(raw, dict):
        raise TeamValidationError(f"{source} 的 members 每一项必须是表")
    allowed = {"id", "role", "model", "effort", "speed", "responsibility"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise TeamValidationError(f"{source} 的成员含未知字段: {', '.join(unknown)}")
    member_id = validate_team_id(_string(raw, "id", source))
    role = _string(raw, "role", source)
    if role not in _ROLES:
        raise TeamValidationError(f"{source} 的成员 {member_id} 的 role 必须是 leader 或 member")
    effort = _string(raw, "effort", source)
    if effort not in _EFFORTS:
        raise TeamValidationError(
            f"{source} 的成员 {member_id} 的 effort 必须是: {', '.join(sorted(_EFFORTS))}"
        )
    speed = _string(raw, "speed", source)
    if speed not in _SPEEDS:
        raise TeamValidationError(
            f"{source} 的成员 {member_id} 的 speed 必须是: {', '.join(sorted(_SPEEDS))}"
        )
    return TeamMember(
        id=member_id,
        role=role,
        model=_string(raw, "model", source),
        effort=effort,
        speed=speed,
        responsibility=_string(raw, "responsibility", source),
    )


def _string(raw: dict[str, Any], key: str, source: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TeamValidationError(f"{source} 的 {key} 必须是非空字符串")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str, source: str) -> str:
    if key not in raw:
        return ""
    return _string(raw, key, source)
