"""TEAM-003: 将 Fable 团队安全投影为工作区运行名册。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from roster.schema import Member, Roster
from roster.start import member_env
from team.activation import TeamRuntimeError, activate_team, roster_for_team
from team.binding import bind_team, load_team_binding
from team.store import DEFAULT_TEAM_ID, TeamStore
from workspace.members import WorkspaceMembers, load_workspace_members, save_workspace_members
from workspace.store import Store


class FakeTmux:
    """最小 tmux 替身；接收的是已经命名空间化的真实会话名。"""

    def __init__(self, sessions: tuple[str, ...] = ()) -> None:
        self.sessions = set(sessions)
        self.calls: list[tuple[str, str]] = []

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, **_kwargs: object) -> None:
        self.sessions.add(name)
        self.calls.append(("new", name))

    def kill_session(self, name: str, **_kwargs: object) -> None:
        self.sessions.discard(name)
        self.calls.append(("kill", name))


def _workspace(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    workspace, _ = store.add(project, slug="project")
    teams = TeamStore(store.home)
    teams.init_default()
    return workspace, teams


def _old_roster() -> Roster:
    return Roster(
        members=(
            Member(name="claude", command="claude"),
            Member(name="codex", command="codex"),
        )
    )


def test_default_fable_team_uses_only_claude_and_codex_runners(tmp_path: Path) -> None:
    _workspace_obj, teams = _workspace(tmp_path)

    roster = roster_for_team(teams.load(DEFAULT_TEAM_ID))

    assert [(member.name, member.command) for member in roster.members] == [
        ("fable", "claude"),
        ("sonnet", "claude"),
        ("opus", "claude"),
        ("luna", "codex"),
        ("sol", "codex"),
    ]
    assert all(member.command != "agent" for member in roster.members)
    assert roster.get("fable").team_id == DEFAULT_TEAM_ID
    assert roster.get("fable").role == "leader"
    assert roster.get("fable").leader_name == "fable"
    assert roster.get("fable").model == "Claude Fable 5"
    assert "最终责任" in roster.get("fable").responsibility
    assert "`sonnet`: role=member, model=Sonnet" in roster.get("fable").team_roster
    assert "effort=xhigh, speed=standard" in roster.get("fable").team_roster
    assert roster.get("sol").role == "member"
    assert roster.get("sol").leader_name == "fable"
    fable_env = member_env(roster.get("fable"))
    assert {key: fable_env[key] for key in (
        "AGENT_NAME",
        "AGENT_ROLE",
        "AMUX_TEAM_ID",
        "AMUX_TEAM_LEADER",
    )} == {
        "AGENT_NAME": "fable",
        "AGENT_ROLE": "leader",
        "AMUX_TEAM_ID": DEFAULT_TEAM_ID,
        "AMUX_TEAM_LEADER": "fable",
    }
    assert dict(roster.get("fable").env) == {
        "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
        "NO_COLOR": "",
    }
    assert dict(roster.get("sonnet").env) == {
        "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
        "NO_COLOR": "",
    }
    assert dict(roster.get("opus").env) == {
        "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
        "NO_COLOR": "",
    }
    assert dict(roster.get("luna").env) == {}
    assert dict(roster.get("sol").env) == {}
    assert roster.get("fable").args[:4] == ("--model", "fable", "--effort", "high")
    assert roster.get("fable").args[-2:] == ("--permission-mode", "auto")
    assert roster.get("sonnet").args[:4] == ("--model", "sonnet", "--effort", "xhigh")
    assert roster.get("sonnet").args[-2:] == ("--permission-mode", "auto")
    assert roster.get("opus").args[:4] == ("--model", "opus", "--effort", "high")
    assert roster.get("opus").args[-2:] == ("--permission-mode", "auto")
    assert roster.get("luna").args[:2] == ("-m", "gpt-5.6-luna")
    assert 'service_tier="priority"' in roster.get("luna").args
    assert roster.get("luna").args[-4:] == ("-s", "danger-full-access", "-a", "never")
    assert roster.get("sol").args[:2] == ("-m", "gpt-5.6-sol")
    assert 'model_reasoning_effort="xhigh"' in roster.get("sol").args
    assert roster.get("sol").args[-4:] == ("-s", "danger-full-access", "-a", "never")


def test_activate_replaces_only_old_enabled_roster_and_starts_fable_team(tmp_path: Path) -> None:
    workspace, teams = _workspace(tmp_path)
    tmux = FakeTmux(("claude@project", "codex@project", "unrelated@other"))

    activation = activate_team(
        workspace,
        DEFAULT_TEAM_ID,
        teams=teams,
        tmux=tmux,
        old_roster=_old_roster(),
        command_exists=lambda _command: "/bin/tool",
    )

    assert [result.name for result in activation.stopped] == ["claude", "codex"]
    assert all(result.changed for result in activation.stopped)
    assert [result.name for result in activation.started] == [
        "fable",
        "sonnet",
        "opus",
        "luna",
        "sol",
    ]
    assert all(result.changed for result in activation.started)
    assert tmux.sessions == {
        "fable@project",
        "sonnet@project",
        "opus@project",
        "luna@project",
        "sol@project",
        "unrelated@other",
    }
    stored = load_workspace_members(workspace)
    assert stored is not None
    assert stored.names == ("fable", "sonnet", "opus", "luna", "sol")
    assert [member.command for member in stored.custom] == [
        "claude",
        "claude",
        "claude",
        "codex",
        "codex",
    ]
    assert stored.custom[0].env["CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"] == "1"
    assert stored.custom[0].env["NO_COLOR"] == ""
    assert stored.custom[0].env["AGENT_ROLE"] == "leader"
    assert stored.custom[0].env["AMUX_TEAM_ID"] == DEFAULT_TEAM_ID
    assert stored.custom[0].team_id == DEFAULT_TEAM_ID
    assert stored.custom[0].role == "leader"
    assert stored.custom[0].leader_name == "fable"
    assert stored.custom[0].model == "Claude Fable 5"
    assert "最终责任" in stored.custom[0].responsibility
    assert stored.custom[0].team_roster == roster_for_team(
        teams.load(DEFAULT_TEAM_ID)
    ).get("fable").team_roster
    assert stored.custom[3].role == "member"
    assert stored.custom[3].leader_name == "fable"
    assert stored.custom[3].env["AGENT_ROLE"] == "member"
    assert stored.custom[3].env["AMUX_TEAM_LEADER"] == "fable"
    assert load_team_binding(workspace).team_id == DEFAULT_TEAM_ID

    raw = tomllib.loads((workspace.state_dir / "members.toml").read_text(encoding="utf-8"))
    old_allowed = {
        "name",
        "command",
        "args",
        "env",
        "greeting_template",
        "enabled",
        "auto_respawn",
    }
    assert all(set(item) <= old_allowed for item in raw["custom"])
    assert raw["custom"][0]["env"]["AGENT_ROLE"] == "leader"
    assert raw["custom"][4]["env"]["AGENT_ROLE"] == "member"


def test_missing_runner_keeps_existing_binding_and_members_untouched(tmp_path: Path) -> None:
    workspace, teams = _workspace(tmp_path)
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    save_workspace_members(
        workspace,
        WorkspaceMembers(names=("claude",), custom=(Member(name="claude", command="claude"),)),
    )
    tmux = FakeTmux(("claude@project",))

    with pytest.raises(TeamRuntimeError, match="找不到团队所需命令: codex"):
        activate_team(
            workspace,
            DEFAULT_TEAM_ID,
            teams=teams,
            tmux=tmux,
            old_roster=_old_roster(),
            command_exists=lambda command: "/bin/claude" if command == "claude" else None,
        )

    assert tmux.sessions == {"claude@project"}
    assert load_team_binding(workspace).team_id == DEFAULT_TEAM_ID
    stored = load_workspace_members(workspace)
    assert stored is not None and stored.names == ("claude",)
