"""WS-011:当前目录即工作区;成员默认空,由用户增减。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from bus import pending, read_message
from bus.paths import BusPaths
from console.cli import bind_runtime
from console.cli import main as amux_main
from console.commands import COMMAND_NAMES, CommandRunner
from console.members import member_names
from roster.load import load_effective_roster, load_roster
from roster.schema import RosterError
from workspace.members import add_member, load_workspace_members, remove_member
from workspace.paths import ENV_AMUX_HOME
from workspace.resolve import ensure_from_cwd
from workspace.store import Store


def store_at(tmp_path: Path) -> Store:
    return Store(tmp_path / "amux-home")


def test_bind_runtime_registers_unregistered_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "other-app"
    project.mkdir()
    paths, workspace = bind_runtime(cwd=project)
    assert workspace is not None
    assert workspace.project_root == project.resolve()
    assert workspace.slug == "other-app"
    assert paths.root == BusPaths.for_workspace(workspace).root
    assert Store.default().get_by_path(project) is not None


def test_bind_runtime_does_not_fall_back_to_amux_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "elsewhere"
    project.mkdir()
    _paths, workspace = bind_runtime(cwd=project)
    assert workspace is not None
    assert workspace.project_root != Path(__file__).resolve().parents[1]


def test_new_workspace_has_no_members_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "blank"
    project.mkdir()
    ensure_from_cwd(project)
    assert member_names(cwd=project) == ()
    assert [m.name for m in load_effective_roster(cwd=project).enabled_members()] == []


def test_amux_toml_still_pins_members_when_no_members_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "pinned"
    project.mkdir()
    (project / "amux.toml").write_text('enabled = ["codex"]\n', encoding="utf-8")
    ensure_from_cwd(project)
    assert member_names(cwd=project) == ("codex",)


def test_member_add_preset_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "demo"
    project.mkdir()
    workspace = ensure_from_cwd(project)
    presets = load_roster()
    added, created = add_member(workspace, "claude", presets=presets)
    assert created is True
    assert added.name == "claude"
    assert member_names(cwd=project) == ("claude",)
    stored = load_workspace_members(workspace)
    assert stored is not None and stored.names == ("claude",)
    remove_member(workspace, "claude", presets=presets)
    assert member_names(cwd=project) == ()


def test_member_add_unknown_preset_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "demo"
    project.mkdir()
    workspace, _ = store_at(tmp_path).add(project)
    with pytest.raises(RosterError, match="--command"):
        add_member(workspace, "nope", presets=load_roster())


def test_member_add_custom_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "demo"
    project.mkdir()
    workspace, _ = store_at(tmp_path).add(project)
    add_member(
        workspace,
        "bot",
        presets=load_roster(),
        command="cat",
        args=("-n",),
    )
    roster = load_effective_roster(cwd=project)
    bot = roster.get("bot")
    assert bot is not None and bot.enabled
    assert bot.command == "cat"
    assert bot.args == ("-n",)
    assert roster.get("claude") is not None and roster.get("claude").enabled is False


def test_cli_member_add_list_rm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workspace.member_cli import main as member_main

    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    out = io.StringIO()
    assert member_main(["add", "claude"], stdout=out) == 0
    assert "已加入 claude" in out.getvalue()
    out = io.StringIO()
    assert member_main(["list"], stdout=out) == 0
    assert "claude" in out.getvalue()
    out = io.StringIO()
    assert member_main(["rm", "claude"], stdout=out) == 0
    assert "拿掉 claude" in out.getvalue()
    assert member_names(cwd=project) == ()


def test_amux_msg_from_unregistered_dir_stays_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    monkeypatch.delenv("BUS_ROOT", raising=False)
    project = tmp_path / "foreign"
    project.mkdir()
    monkeypatch.chdir(project)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert amux_main(["msg", "human", "就在这里"]) == 0
    workspace = Store.default().get_by_path(project)
    assert workspace is not None
    queued = pending(BusPaths.for_workspace(workspace))
    assert read_message(queued[0]).text == "就在这里"


def test_member_is_a_first_class_command() -> None:
    assert "member" in COMMAND_NAMES
    runner = CommandRunner()
    assert "不可用" in runner.run("/member add claude")[0]
    assert "用法:/member add <名字>" in runner.run("/member add")[0]
    help_text = "\n".join(runner.run("/help"))
    assert "/member" in help_text
