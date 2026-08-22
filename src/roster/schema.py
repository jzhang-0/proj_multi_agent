"""成员名册 schema 与校验。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

RESERVED_NAMES = frozenset({"human", "bus"})
ENV_AGENT_ROLE = "AGENT_ROLE"
ENV_TEAM_ID = "AMUX_TEAM_ID"
ENV_TEAM_LEADER = "AMUX_TEAM_LEADER"
ENV_AGENT_MODEL = "AMUX_AGENT_MODEL"
ENV_AGENT_RESPONSIBILITY = "AMUX_AGENT_RESPONSIBILITY"
ENV_TEAM_ROSTER = "AMUX_TEAM_ROSTER"


class RosterError(ValueError):
    """roster.toml 结构非法。"""


@dataclass(frozen=True)
class Member:
    """一个成员的声明。`name` 是总线短名;tmux 会话名由 `workspace.session` 映射。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    team_id: str = ""
    role: str = ""
    leader_name: str = ""
    model: str = ""
    responsibility: str = ""
    team_roster: str = ""
    greeting_template: str = ""
    enabled: bool = True
    auto_respawn: bool = False

    def render_greeting(
        self,
        *,
        protocol: str | None = None,
        workspace_slug: str | None = None,
        project_root: str | None = None,
    ) -> str:
        """从 prompts 资源生成开场白；模板只作为可选的成员前言。"""
        from roster.protocol import render_member_greeting

        return render_member_greeting(
            self.name,
            intro_template=self.greeting_template,
            protocol=protocol,
            workspace_slug=workspace_slug,
            project_root=project_root,
            team_id=self.team_id,
            role=self.role,
            leader_name=self.leader_name,
            model=self.model,
            responsibility=self.responsibility,
            team_roster=self.team_roster,
        )


@dataclass(frozen=True)
class Roster:
    """一份已校验的名册。"""

    members: tuple[Member, ...]
    source: str = "roster.toml"

    def enabled_members(self) -> tuple[Member, ...]:
        return tuple(member for member in self.members if member.enabled)

    def get(self, name: str) -> Member | None:
        for member in self.members:
            if member.name == name:
                return member
        return None


def _require_str(raw: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    if key not in raw:
        raise RosterError(f"缺少字段 {key}")
    value = raw[key]
    if not isinstance(value, str):
        raise RosterError(f"字段 {key} 必须是字符串,实际是 {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise RosterError(f"字段 {key} 不能为空")
    return value


def _optional_bool(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise RosterError(f"字段 {key} 必须是布尔值,实际是 {type(value).__name__}")
    return value


def _optional_str_list(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    if key not in raw:
        return ()
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RosterError(f"字段 {key} 必须是字符串数组")
    return tuple(value)


def _optional_env(raw: Mapping[str, Any]) -> dict[str, str]:
    if "env" not in raw:
        return {}
    value = raw["env"]
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise RosterError("字段 env 必须是字符串到字符串的表")
    return dict(value)


def _optional_str(raw: Mapping[str, Any], key: str) -> str:
    if key not in raw:
        return ""
    return _require_str(raw, key)


def validate_member_name(name: str) -> str:
    """校验可同时作为总线收件人和 tmux 会话名主体的成员名。"""
    if name in RESERVED_NAMES:
        raise RosterError(f"成员名 {name!r} 是保留名,不能进名册")
    if ":" in name:
        raise RosterError(
            f"成员名 {name!r} 含非法字符 ':'(tmux 会把它当成 session:window 分隔符,"
            "可能静默命中已有会话)"
        )
    if "." in name:
        raise RosterError(
            f"成员名 {name!r} 含非法字符 '.'(tmux 会把它静默改写成 '_')"
        )
    if "@" in name:
        raise RosterError(f"成员名 {name!r} 不能含 '@'(会话名格式是 成员@工作区)")
    if any(ch in name for ch in "\n\t "):
        raise RosterError(f"成员名 {name!r} 不能含空格或换行(必须可做 tmux 会话名)")
    return name


def member_from_dict(raw: Any, *, default_greeting: str) -> Member:
    if not isinstance(raw, dict):
        raise RosterError("members 的每一项必须是表")
    name = validate_member_name(_require_str(raw, "name"))
    command = _require_str(raw, "command")
    greeting = raw.get("greeting_template", default_greeting)
    if not isinstance(greeting, str):
        raise RosterError(f"成员 {name} 的开场白模板必须是字符串")
    env = _optional_env(raw)
    role = _optional_str(raw, "role") or env.get(ENV_AGENT_ROLE, "")
    if role not in {"", "leader", "member"}:
        raise RosterError(f"成员 {name} 的 role 必须是 leader 或 member")
    team_id = _optional_str(raw, "team_id") or env.get(ENV_TEAM_ID, "")
    leader_name = _optional_str(raw, "leader") or env.get(ENV_TEAM_LEADER, "")
    model = _optional_str(raw, "model") or env.get(ENV_AGENT_MODEL, "")
    responsibility = _optional_str(raw, "responsibility") or env.get(
        ENV_AGENT_RESPONSIBILITY, ""
    )
    team_roster = _optional_str(raw, "team_roster") or env.get(ENV_TEAM_ROSTER, "")
    metadata = (team_id, leader_name, model, responsibility, team_roster)
    if role and not all(metadata):
        raise RosterError(
            f"成员 {name} 设置 role 时必须同时设置 team_id、leader、model、"
            "responsibility、team_roster"
        )
    if not role and any(metadata):
        raise RosterError(f"成员 {name} 的团队元数据必须与 role 一起设置")
    if role == "leader" and leader_name != name:
        raise RosterError(f"Leader 成员 {name} 的 leader 必须指向自己")
    known = {
        "name",
        "command",
        "args",
        "env",
        "team_id",
        "role",
        "leader",
        "model",
        "responsibility",
        "team_roster",
        "greeting_template",
        "enabled",
        "auto_respawn",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise RosterError(f"成员 {name} 含未知字段: {', '.join(unknown)}")
    return Member(
        name=name,
        command=command,
        args=_optional_str_list(raw, "args"),
        env=env,
        team_id=team_id,
        role=role,
        leader_name=leader_name,
        model=model,
        responsibility=responsibility,
        team_roster=team_roster,
        greeting_template=greeting,
        enabled=_optional_bool(raw, "enabled", True),
        auto_respawn=_optional_bool(raw, "auto_respawn", False),
    )


def member_metadata_env(member: Member) -> dict[str, str]:
    """把角色元数据编码到 0.1.x 已允许的 env 表，保持共享名册向后兼容。"""
    if not member.role:
        return {}
    return {
        ENV_AGENT_ROLE: member.role,
        ENV_TEAM_ID: member.team_id,
        ENV_TEAM_LEADER: member.leader_name,
        ENV_AGENT_MODEL: member.model,
        ENV_AGENT_RESPONSIBILITY: member.responsibility,
        ENV_TEAM_ROSTER: member.team_roster,
    }


def roster_from_dict(raw: Any, *, source: str = "roster.toml") -> Roster:
    if not isinstance(raw, dict):
        raise RosterError("roster.toml 根必须是表")
    default_greeting = raw.get("default_greeting_template", "")
    if default_greeting is None:
        default_greeting = ""
    if not isinstance(default_greeting, str):
        raise RosterError("default_greeting_template 必须是字符串")
    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or not members_raw:
        raise RosterError("roster.toml 必须有非空的 members 数组")
    members = tuple(
        member_from_dict(item, default_greeting=default_greeting) for item in members_raw
    )
    names = [member.name for member in members]
    dupes = {name for name in names if names.count(name) > 1}
    if dupes:
        raise RosterError(f"成员名重复: {', '.join(sorted(dupes))}")
    return Roster(members=members, source=source)
