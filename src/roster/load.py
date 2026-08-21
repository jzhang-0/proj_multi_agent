"""加载 roster.toml,并按工作区成员名单 / 项目侧 amux.toml 做覆盖。

合并规则(写进架构 §5,这里是实现):

1. 仓库根 `roster.toml` 是预设目录(四个 CLI 怎么启动),不自动启用。
2. 工作区 `~/.amux/workspaces/<slug>/members.toml` 是用户增减的名单;
   有这份文件就以它为准。
3. 没有 members.toml 时,项目根可选 `amux.toml` 的 `enabled` 仍可钉一份名单
   (本仓库自己用它保留四成员协作)。
4. 前两者都没有时,采用 `~/.amux/config.toml` 的 `default_members`。
5. 三份配置都没有 = 空名册,一个人都不会被拉起。
6. `[env]` 覆盖到每个启用成员的 env 上,项目侧同名键赢。
"""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from roster.paths import default_path
from roster.schema import Member, Roster, RosterError, roster_from_dict
from workspace.config import ProjectConfig, load_project_config
from workspace.global_config import load_global_config
from workspace.members import WorkspaceMembers, load_workspace_members
from workspace.resolve import resolve_from_cwd


def load_roster(path: str | Path | None = None) -> Roster:
    """读并校验名册。默认仓库根 `roster.toml`。"""
    target = Path(path) if path is not None else default_path()
    if not target.is_file():
        raise RosterError(f"找不到名册文件: {target}")
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RosterError(f"无法解析 {target}: {exc}") from exc
    return roster_from_dict(raw, source=str(target))


def apply_overlay(roster: Roster, config: ProjectConfig) -> Roster:
    """把项目侧 amux.toml 盖到全局名册上。"""
    if config.enabled is not None:
        known = {member.name for member in roster.members}
        unknown = [name for name in config.enabled if name not in known]
        if unknown:
            raise RosterError(
                f"amux.toml 的 enabled 含名册里没有的成员: {', '.join(unknown)}"
            )
        enabled = set(config.enabled)
    else:
        enabled = None

    extra = dict(config.env)
    members: list[Member] = []
    for member in roster.members:
        members.append(
            replace(
                member,
                enabled=member.enabled if enabled is None else member.name in enabled,
                env={**dict(member.env), **extra},
            )
        )
    source = roster.source if config.source is None else f"{roster.source}+{config.source}"
    return Roster(members=tuple(members), source=source)


def assemble_workspace_roster(
    presets: Roster,
    stored: WorkspaceMembers,
    config: ProjectConfig,
) -> Roster:
    """按工作区 names 启用预设或自定义成员,其余预设保持停用。"""
    extra = dict(config.env)
    custom_by_name = {member.name: member for member in stored.custom}
    selected: list[Member] = []
    seen: set[str] = set()
    for name in stored.names:
        if name in custom_by_name:
            member = custom_by_name[name]
        else:
            member = presets.get(name)
            if member is None:
                raise RosterError(
                    f"工作区名册含未知成员 {name!r}(不是预设也没有 command)"
                )
        selected.append(
            replace(member, enabled=True, env={**dict(member.env), **extra})
        )
        seen.add(name)
    rest = [
        replace(member, enabled=False, env={**dict(member.env), **extra})
        for member in presets.members
        if member.name not in seen
    ]
    source = stored.source or "members.toml"
    if config.source:
        source = f"{source}+{config.source}"
    return Roster(members=tuple(selected + rest), source=source)


def load_effective_roster(
    path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
) -> Roster:
    """预设 + 工作区/项目/全局成员名单;都没有则空名册。"""
    presets = load_roster(path)
    workspace = resolve_from_cwd(cwd)
    config = (
        load_project_config(workspace.project_root) if workspace is not None else ProjectConfig()
    )
    stored = load_workspace_members(workspace) if workspace is not None else None
    if stored is not None:
        return assemble_workspace_roster(presets, stored, config)
    if config.enabled is not None:
        return apply_overlay(presets, config)
    defaults = load_global_config().default_members
    if defaults:
        return apply_overlay(presets, ProjectConfig(enabled=defaults, env=config.env))
    empty = ProjectConfig(enabled=(), env=config.env, source=config.source)
    return apply_overlay(presets, empty)
