"""按名册拉起/关掉成员会话。生命周期幂等 API 见 ROS-003;这里保持 v0 start.sh 行为。"""

from __future__ import annotations

import shlex
from pathlib import Path

from roster.paths import repo_root
from roster.schema import Member, Roster
from tmuxctl import Tmux


def window_command(member: Member) -> str:
    """v0 同款:在默认 shell 里跑 CLI,把开场白当第一个参数,退出后留在 shell。"""
    prompt = member.render_greeting()
    argv = [member.command, *member.args]
    return f"{shlex.join(argv)} {shlex.quote(prompt)}; exec $SHELL"


def start_member(member: Member, tmux: Tmux, *, cwd: Path | None = None) -> str:
    """拉起一名成员。已在跑则跳过。返回说明字符串。"""
    if tmux.has_session(member.name):
        return f"[start] {member.name} 已在运行,跳过"
    env = {**dict(member.env), "AGENT_NAME": member.name}
    tmux.new_session(
        member.name,
        command=window_command(member),
        detached=True,
        cwd=str(cwd or repo_root()),
        env=env,
    )
    return f"[start] {member.name} 已启动 (查看: tmux attach -t {member.name})"


def stop_member(member: Member, tmux: Tmux) -> str:
    if not tmux.has_session(member.name):
        return f"[stop] {member.name} 未运行"
    tmux.kill_session(member.name, missing_ok=True)
    return f"[stop] {member.name} 已关闭"


def start_all(roster: Roster, tmux: Tmux, *, cwd: Path | None = None) -> list[str]:
    return [start_member(member, tmux, cwd=cwd) for member in roster.enabled_members()]


def stop_all(roster: Roster, tmux: Tmux) -> list[str]:
    return [stop_member(member, tmux) for member in roster.members]
