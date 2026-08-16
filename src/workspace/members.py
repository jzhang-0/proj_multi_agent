"""每个工作区自己的成员名单,默认空。

四个 CLI 的启动参数在 amux 仓库根 `roster.toml` 里当预设;用户
`amux member add claude` 才启用。自定义成员用 `--command` 写进本文件。
状态在 `~/.amux/workspaces/<slug>/members.toml`,不写进用户项目。
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace

from roster.schema import Member, Roster, RosterError, member_from_dict, validate_member_name
from workspace.errors import WorkspaceError
from workspace.model import Workspace
from workspace.paths import MEMBERS_NAME


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class WorkspaceMembers:
    """一份工作区成员名单。`names` 是启用顺序;自定义成员另存 command。"""

    names: tuple[str, ...] = ()
    custom: tuple[Member, ...] = ()
    source: str | None = None


def members_file(workspace: Workspace):
    return workspace.state_dir / MEMBERS_NAME


def load_workspace_members(workspace: Workspace) -> WorkspaceMembers | None:
    """读工作区 members.toml;文件不存在返回 None(还没用过增减命令)。"""
    target = members_file(workspace)
    if not target.is_file():
        return None
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"无法解析 {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{target} 根必须是表")
    names = _names(raw, target)
    custom_raw = raw.get("custom", [])
    if not isinstance(custom_raw, list):
        raise WorkspaceError(f"{target} 的 custom 必须是数组")
    custom = tuple(
        member_from_dict(item, default_greeting="") for item in custom_raw
    )
    return WorkspaceMembers(names=names, custom=custom, source=str(target))


def save_workspace_members(workspace: Workspace, members: WorkspaceMembers) -> None:
    """把名单写回状态目录,不碰项目文件。"""
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 本工作区启用的成员。预设启动参数见 amux 仓库 roster.toml。",
        f"names = [{', '.join(_toml_str(name) for name in members.names)}]",
    ]
    for member in members.custom:
        lines.append("")
        lines.append("[[custom]]")
        lines.append(f"name = {_toml_str(member.name)}")
        lines.append(f"command = {_toml_str(member.command)}")
        if member.args:
            args = ", ".join(_toml_str(arg) for arg in member.args)
            lines.append(f"args = [{args}]")
        if member.auto_respawn:
            lines.append("auto_respawn = true")
        if member.env:
            lines.append("[custom.env]")
            for key, value in member.env.items():
                lines.append(f"{key} = {_toml_str(value)}")
    members_file(workspace).write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_member(
    workspace: Workspace,
    name: str,
    *,
    presets: Roster,
    command: str | None = None,
    args: Sequence[str] = (),
) -> tuple[Member, bool]:
    """启用一个成员。已在名单里则原样返回 created=False。"""
    chosen = validate_member_name(name)
    current = load_workspace_members(workspace) or WorkspaceMembers()
    if chosen in current.names:
        existing = _resolve_one(chosen, current, presets)
        return existing, False
    if command:
        member = Member(name=chosen, command=command, args=tuple(args), enabled=True)
        custom = tuple(item for item in current.custom if item.name != chosen) + (member,)
    else:
        preset = presets.get(chosen)
        if preset is None:
            known = ", ".join(item.name for item in presets.members)
            raise RosterError(
                f"没有叫 {chosen!r} 的预设。可用预设: {known}。"
                "自定义成员请加 --command"
            )
        member = replace(preset, enabled=True)
        custom = current.custom
    updated = WorkspaceMembers(
        names=(*current.names, chosen),
        custom=custom,
        source=str(members_file(workspace)),
    )
    save_workspace_members(workspace, updated)
    return member, True


def remove_member(
    workspace: Workspace,
    name: str,
    *,
    presets: Roster,
) -> Member:
    """从本工作区名单里拿掉,不关会话、不删预设。"""
    current = load_workspace_members(workspace) or WorkspaceMembers()
    if name not in current.names:
        raise RosterError(f"本工作区没有成员 {name!r}")
    removed = _resolve_one(name, current, presets)
    save_workspace_members(
        workspace,
        WorkspaceMembers(
            names=tuple(item for item in current.names if item != name),
            custom=tuple(item for item in current.custom if item.name != name),
        ),
    )
    return removed


def _names(raw: dict[str, object], target) -> tuple[str, ...]:
    if "names" not in raw:
        return ()
    value = raw["names"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkspaceError(f"{target} 的 names 必须是字符串数组")
    names = tuple(item.strip() for item in value)
    if any(not name for name in names):
        raise WorkspaceError(f"{target} 的 names 不能含空字符串")
    dupes = {name for name in names if names.count(name) > 1}
    if dupes:
        raise WorkspaceError(f"{target} 的 names 成员名重复: {', '.join(sorted(dupes))}")
    return names


def _resolve_one(name: str, stored: WorkspaceMembers, presets: Roster) -> Member:
    for custom in stored.custom:
        if custom.name == name:
            return replace(custom, enabled=True)
    preset = presets.get(name)
    if preset is None:
        raise RosterError(f"没有叫 {name!r} 的成员")
    return replace(preset, enabled=True)
