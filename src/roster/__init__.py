"""成员名册:配置加载与 start.sh 薄入口。"""

from roster.adopt import SessionAdopter, SessionCandidate, TemporaryMember
from roster.health import HealthState, HealthSupervisor, HealthUpdate
from roster.load import load_effective_roster, load_roster
from roster.protocol import (
    COLLABORATION_PROTOCOL_HEADING,
    ProtocolSourceError,
    check_single_source,
    extract_chat_protocol,
    extract_collaboration_protocol,
    load_chat_protocol,
    load_collaboration_protocol,
    load_prompt,
    render_member_greeting,
)
from roster.schema import Member, Roster, RosterError
from roster.start import member_env, start_all, start_member, stop_all, stop_member, window_command

__all__ = [
    "HealthState",
    "HealthSupervisor",
    "HealthUpdate",
    "Member",
    "COLLABORATION_PROTOCOL_HEADING",
    "ProtocolSourceError",
    "Roster",
    "RosterError",
    "SessionAdopter",
    "SessionCandidate",
    "TemporaryMember",
    "check_single_source",
    "extract_collaboration_protocol",
    "extract_chat_protocol",
    "load_effective_roster",
    "load_roster",
    "load_collaboration_protocol",
    "load_chat_protocol",
    "load_prompt",
    "member_env",
    "render_member_greeting",
    "start_all",
    "start_member",
    "stop_all",
    "stop_member",
    "window_command",
]
