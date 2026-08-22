"""TEAM-001: 团队档案、绑定和命令行。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from team.binding import bind_team, load_team_binding
from team.cli import main as team_main
from team.model import TeamValidationError, team_from_dict
from team.store import DEFAULT_TEAM_ID, TeamNotFound, TeamStore
from workspace.store import Store


def _workspace(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    return store, store.add(project, slug="project")[0], project


def test_default_team_records_the_agreed_leader_and_members(tmp_path: Path) -> None:
    teams = TeamStore(tmp_path / "amux-home")
    target = teams.init_default()

    assert target == teams.path_for(DEFAULT_TEAM_ID)
    team = teams.load(DEFAULT_TEAM_ID)
    assert team.leader == "fable"
    assert team.leader_member.model == "Claude Fable 5"
    assert team.leader_member.effort == "high"
    assert [(member.id, member.effort, member.speed) for member in team.members[1:]] == [
        ("sonnet", "xhigh", "standard"),
        ("opus", "high", "standard"),
        ("luna", "high", "fast"),
        ("sol", "xhigh", "standard"),
    ]
    assert "接管" in team.leader_member.responsibility
    for member in team.members[:3]:
        assert dict(member.env) == {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"}
    for member in team.members[3:]:
        assert dict(member.env) == {}
    with pytest.raises(Exception, match="已存在"):
        teams.init_default()


def test_team_schema_requires_one_leader_and_one_member() -> None:
    raw = {
        "id": "single",
        "name": "坏团队",
        "leader": "one",
        "members": [
            {
                "id": "one",
                "role": "leader",
                "model": "Fable",
                "effort": "high",
                "speed": "standard",
                "responsibility": "负责",
            }
        ],
    }
    with pytest.raises(TeamValidationError, match="至少需要一名"):
        team_from_dict(raw, source="memory")

    raw["members"].append(
        {
            "id": "two",
            "role": "leader",
            "model": "Opus",
            "effort": "high",
            "speed": "standard",
            "responsibility": "也负责",
        }
    )
    with pytest.raises(TeamValidationError, match="恰有一名"):
        team_from_dict(raw, source="memory")


def test_team_schema_rejects_invalid_runtime_env() -> None:
    raw = {
        "id": "invalid-env",
        "name": "坏环境",
        "leader": "one",
        "members": [
            {
                "id": "one",
                "role": "leader",
                "model": "Fable",
                "effort": "high",
                "speed": "standard",
                "responsibility": "负责",
                "command": "claude",
                "env": {"FLAG": 1},
            },
            {
                "id": "two",
                "role": "member",
                "model": "Opus",
                "effort": "high",
                "speed": "standard",
                "responsibility": "实现",
            },
        ],
    }
    with pytest.raises(TeamValidationError, match="env 必须"):
        team_from_dict(raw, source="memory")


def test_binding_validates_before_replacing_existing_team(tmp_path: Path) -> None:
    store, workspace, _ = _workspace(tmp_path)
    teams = TeamStore(store.home)
    teams.init_default()
    binding = bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)

    assert binding.team_id == DEFAULT_TEAM_ID
    with pytest.raises(TeamNotFound, match="没有叫"):
        bind_team(workspace, "missing", teams=teams)
    assert load_team_binding(workspace) is not None
    assert load_team_binding(workspace).team_id == DEFAULT_TEAM_ID
    assert (workspace.state_dir / "team.toml").is_file()


def test_cli_initializes_lists_shows_and_binds_current_workspace(tmp_path: Path) -> None:
    store, workspace, project = _workspace(tmp_path)
    teams = TeamStore(store.home)
    out = io.StringIO()
    err = io.StringIO()

    assert team_main(["init"], teams=teams, store=store, cwd=project, stdout=out, stderr=err) == 0
    assert team_main(["list"], teams=teams, store=store, cwd=project, stdout=out, stderr=err) == 0
    assert team_main(
        ["show", DEFAULT_TEAM_ID], teams=teams, store=store, cwd=project, stdout=out, stderr=err
    ) == 0
    assert team_main(
        ["use", DEFAULT_TEAM_ID], teams=teams, store=store, cwd=project, stdout=out, stderr=err
    ) == 0
    assert (
        team_main(
            ["current"], teams=teams, store=store, cwd=project, stdout=out, stderr=err
        )
        == 0
    )

    text = out.getvalue()
    assert "Fable 协作组" in text
    assert "Claude Fable 5" in text
    assert f"工作区 {workspace.slug} 已绑定团队 {DEFAULT_TEAM_ID}" in text
    assert "Leader: Claude Fable 5" in text
    assert err.getvalue() == ""


def test_current_reports_absent_binding_without_creating_one(tmp_path: Path) -> None:
    store, workspace, project = _workspace(tmp_path)
    out = io.StringIO()

    assert team_main(["current"], store=store, cwd=project, stdout=out) == 0
    assert f"工作区 {workspace.slug} 尚未选择团队。" in out.getvalue()
    assert not (workspace.state_dir / "team.toml").exists()
