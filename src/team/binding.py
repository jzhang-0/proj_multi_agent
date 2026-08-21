"""工作区到团队档案的引用。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from team.model import validate_team_id
from team.store import TeamStore
from workspace.errors import WorkspaceError
from workspace.model import Workspace
from workspace.paths import TEAM_BINDING_NAME


@dataclass(frozen=True)
class TeamBinding:
    """工作区中已选团队的稳定引用。"""

    team_id: str
    source: Path


def binding_path(workspace: Workspace) -> Path:
    return workspace.state_dir / TEAM_BINDING_NAME


def load_team_binding(workspace: Workspace) -> TeamBinding | None:
    """读取工作区引用；尚未选择团队时返回 ``None``。"""
    target = binding_path(workspace)
    if not target.is_file():
        return None
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"无法解析 {target}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"team"}:
        raise WorkspaceError(f"{target} 只能包含一个 team 字段")
    value = raw["team"]
    if not isinstance(value, str):
        raise WorkspaceError(f"{target} 的 team 必须是字符串")
    return TeamBinding(team_id=validate_team_id(value), source=target)


def bind_team(workspace: Workspace, team_id: str, *, teams: TeamStore) -> TeamBinding:
    """校验团队存在后再更新引用，避免无效选择覆盖原绑定。"""
    selected = teams.load(team_id)
    target = binding_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# 此工作区所用的协作团队。团队档案保存在 ~/.amux/teams/。\n"
        f'team = "{selected.id}"\n',
        encoding="utf-8",
    )
    return TeamBinding(team_id=selected.id, source=target)
