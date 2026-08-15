"""成员名册:配置加载与 start.sh 薄入口。"""

from roster.adopt import SessionAdopter, SessionCandidate, TemporaryMember
from roster.health import HealthState, HealthSupervisor, HealthUpdate
from roster.load import load_roster
from roster.protocol import (
    ProtocolSourceError,
    check_single_source,
    extract_chat_protocol,
    load_chat_protocol,
    render_member_greeting,
)
from roster.schema import Member, Roster, RosterError
from roster.start import member_env, start_all, start_member, stop_all, stop_member, window_command

__all__ = [
    "HealthState",
    "HealthSupervisor",
    "HealthUpdate",
    "Member",
    "ProtocolSourceError",
    "Roster",
    "RosterError",
    "SessionAdopter",
    "SessionCandidate",
    "TemporaryMember",
    "check_single_source",
    "extract_chat_protocol",
    "load_roster",
    "load_chat_protocol",
    "member_env",
    "render_member_greeting",
    "start_all",
    "start_member",
    "stop_all",
    "stop_member",
    "window_command",
]
