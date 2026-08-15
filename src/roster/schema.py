"""成员名册 schema 与校验。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

RESERVED_NAMES = frozenset({"human", "bus"})


class RosterError(ValueError):
    """roster.toml 结构非法。"""


@dataclass(frozen=True)
class Member:
    """一个成员的声明。`name` 等于 tmux 会话名。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    greeting_template: str = ""
    enabled: bool = True
    auto_respawn: bool = False

    def render_greeting(self) -> str:
        """用成员名填充开场白模板。"""
        return self.greeting_template.replace("{NAME}", self.name).replace("{name}", self.name)


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


def _validate_name(name: str) -> str:
    if name in RESERVED_NAMES:
        raise RosterError(f"成员名 {name!r} 是保留名,不能进名册")
    if any(ch in name for ch in ":\n\t "):
        raise RosterError(f"成员名 {name!r} 不能含空格或冒号(必须可做 tmux 会话名)")
    return name


def member_from_dict(raw: Any, *, default_greeting: str) -> Member:
    if not isinstance(raw, dict):
        raise RosterError("members 的每一项必须是表")
    name = _validate_name(_require_str(raw, "name"))
    command = _require_str(raw, "command")
    greeting = raw.get("greeting_template", default_greeting)
    if not isinstance(greeting, str) or not greeting.strip():
        raise RosterError(f"成员 {name} 的开场白模板必须是非空字符串")
    known = {
        "name",
        "command",
        "args",
        "env",
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
        env=_optional_env(raw),
        greeting_template=greeting,
        enabled=_optional_bool(raw, "enabled", True),
        auto_respawn=_optional_bool(raw, "auto_respawn", False),
    )


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
    missing_greeting = [m.name for m in members if not m.greeting_template.strip()]
    if missing_greeting:
        names = ", ".join(missing_greeting)
        raise RosterError(f"这些成员没有开场白模板(也没有 default_greeting_template): {names}")
    return Roster(members=members, source=source)
