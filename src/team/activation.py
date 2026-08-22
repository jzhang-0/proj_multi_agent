"""把已保存的团队运行时适配投影为当前工作区名册并启动。"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from roster.lifecycle import Lifecycle, LifecycleResult
from roster.load import load_effective_roster
from roster.schema import Member, Roster
from team.binding import TeamBinding, bind_team
from team.model import Team
from team.store import TeamStore
from tmuxctl import Tmux
from workspace.errors import WorkspaceError
from workspace.members import WorkspaceMembers, save_workspace_members
from workspace.model import Workspace
from workspace.session import SessionNames, bind_tmux


class TeamRuntimeError(WorkspaceError):
    """团队可以被保存但还不能被安全启动。"""


@dataclass(frozen=True)
class Activation:
    """一次替换工作区运行成员的可呈现结果。"""

    team: Team
    binding: TeamBinding
    stopped: tuple[LifecycleResult, ...]
    started: tuple[LifecycleResult, ...]


def roster_for_team(team: Team) -> Roster:
    """将团队中的运行时适配转为 `roster` 的自定义成员。"""
    members: list[Member] = []
    missing: list[str] = []
    for member in team.members:
        if member.command is None:
            missing.append(member.id)
            continue
        members.append(
            Member(
                name=member.id,
                command=member.command,
                args=member.args,
                env=member.env,
            )
        )
    if missing:
        raise TeamRuntimeError(
            f"团队 {team.id} 的成员缺少启动适配: {', '.join(missing)}。"
            "请在 ~/.amux/teams 的团队档案中补齐 command/args"
        )
    return Roster(members=tuple(members), source=f"team:{team.id}")


def validate_runtime(
    roster: Roster,
    *,
    command_exists: Callable[[str], str | None] = shutil.which,
) -> None:
    """在关闭旧会话前确认每个本机 CLI 都可执行。"""
    missing = sorted(
        {member.command for member in roster.members if not command_exists(member.command)}
    )
    if missing:
        raise TeamRuntimeError(f"找不到团队所需命令: {', '.join(missing)}")


def activate_team(
    workspace: Workspace,
    team_id: str,
    *,
    teams: TeamStore,
    tmux: Tmux | None = None,
    old_roster: Roster | None = None,
    command_exists: Callable[[str], str | None] = shutil.which,
) -> Activation:
    """校验后替换当前工作区的已启用成员，并启动目标团队。"""
    team = teams.load(team_id)
    target_roster = roster_for_team(team)
    validate_runtime(target_roster, command_exists=command_exists)

    previous = old_roster or load_effective_roster(cwd=workspace.project_root)
    namespaced_tmux = bind_tmux(tmux, names=SessionNames(slug=workspace.slug))
    stopped = tuple(
        Lifecycle(previous, namespaced_tmux, cwd=workspace.project_root).down_member(member)
        for member in previous.enabled_members()
    )

    save_workspace_members(
        workspace,
        WorkspaceMembers(
            names=tuple(member.name for member in target_roster.members),
            custom=target_roster.members,
            source=f"team:{team.id}",
        ),
    )
    binding = bind_team(workspace, team.id, teams=teams)
    started = tuple(
        Lifecycle(target_roster, namespaced_tmux, cwd=workspace.project_root).up()
    )
    return Activation(team=team, binding=binding, stopped=stopped, started=started)
