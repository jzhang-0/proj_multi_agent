"""加载 roster.toml,并按项目侧 amux.toml 做覆盖。

合并规则(写进架构 §5,这里是实现):

1. 全局名册永远是 amux 仓库根的 `roster.toml`(四个成员的启动参数)。
2. 项目根可以有可选的 `amux.toml`。没有这份文件 = 启用名册里所有 enabled
   成员、不追加 env。
3. `enabled = ["claude", "codex"]` 只启用列出的成员;名册里其他成员改为
   `enabled=false`。名单里出现名册没有的名字则报错。
4. `[env]` 覆盖到每个成员的 env 上,项目侧同名键赢。
"""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from roster.paths import default_path
from roster.schema import Member, Roster, RosterError, roster_from_dict
from workspace.config import ProjectConfig, load_project_config
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


def load_effective_roster(
    path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
) -> Roster:
    """全局名册 + 当前工作区 amux.toml 覆盖。未登记工作区则只有全局名册。"""
    roster = load_roster(path)
    workspace = resolve_from_cwd(cwd)
    if workspace is None:
        return roster
    return apply_overlay(roster, load_project_config(workspace.project_root))
