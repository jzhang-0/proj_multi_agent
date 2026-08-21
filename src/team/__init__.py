"""可保存的 AI 协作团队档案与工作区绑定。"""

from team.activation import Activation, TeamRuntimeError, activate_team, roster_for_team
from team.binding import TeamBinding, bind_team, load_team_binding
from team.model import Team, TeamMember, TeamValidationError
from team.store import DEFAULT_TEAM_ID, TeamNotFound, TeamStore

__all__ = [
    "DEFAULT_TEAM_ID",
    "Activation",
    "Team",
    "TeamBinding",
    "TeamMember",
    "TeamNotFound",
    "TeamStore",
    "TeamRuntimeError",
    "TeamValidationError",
    "bind_team",
    "activate_team",
    "load_team_binding",
    "roster_for_team",
]
