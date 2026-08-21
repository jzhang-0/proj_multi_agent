"""WS-012: ~/.amux/config.toml 全局默认配置。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from console.cli import main as amux_main
from roster.load import load_effective_roster, load_roster
from workspace.errors import WorkspaceError
from workspace.global_config import (
    DEFAULT_MEMBERS,
    config_path,
    init_global_config,
    load_global_config,
)
from workspace.global_config import main as config_main
from workspace.members import add_member
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store


def _project(tmp_path: Path, name: str = "alpha") -> Path:
    project = tmp_path / "projects" / name
    project.mkdir(parents=True)
    return project


def _write_config(home: Path, content: str) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text(content, encoding="utf-8")


def test_init_writes_explicit_opt_in_defaults(tmp_path: Path) -> None:
    home = tmp_path / "amux-home"
    target = init_global_config(home=home)

    assert target == config_path(home)
    loaded = load_global_config(home)
    assert loaded.default_members == DEFAULT_MEMBERS
    assert loaded.auto_start_members is True
    assert loaded.theme == "console-dark"
    with pytest.raises(WorkspaceError, match="已存在"):
        init_global_config(home=home)


def test_global_members_apply_only_when_workspace_has_no_local_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    _write_config(
        home,
        "[workspace]\ndefault_members = [\"claude\", \"codex\"]\n",
    )
    project = _project(tmp_path)
    workspace, _ = Store(home).add(project, slug="alpha")
    monkeypatch.chdir(project)

    assert [member.name for member in load_effective_roster().enabled_members()] == [
        "claude",
        "codex",
    ]

    (project / "amux.toml").write_text('enabled = ["agy"]\n', encoding="utf-8")
    assert [member.name for member in load_effective_roster().enabled_members()] == ["agy"]

    add_member(workspace, "cursor", presets=load_roster())
    assert [member.name for member in load_effective_roster().enabled_members()] == ["cursor"]


def test_config_rejects_bad_schema(tmp_path: Path) -> None:
    home = tmp_path / "amux-home"
    _write_config(home, "[console]\ntheme = \"rainbow\"\n")

    with pytest.raises(WorkspaceError, match="theme"):
        load_global_config(home)


def test_config_command_initializes_and_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    output = io.StringIO()

    assert config_main(["init"], stdout=output) == 0
    assert config_path().is_file()
    assert config_main(["show"], stdout=output) == 0
    assert "自动拉起成员: 是" in output.getvalue()


def test_amux_reads_global_theme_and_auto_starts_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    _write_config(
        home,
        "[workspace]\ndefault_members = [\"claude\"]\n\n"
        "[lifecycle]\nauto_start_members = true\n\n"
        "[console]\ntheme = \"console-light\"\n",
    )
    project = _project(tmp_path)
    workspace, _ = Store(home).add(project, slug="alpha")
    monkeypatch.chdir(project)
    started: list[object] = []
    monkeypatch.setattr("console.cli._auto_start_members", started.append)

    assert amux_main(["--headless", "--once"]) == 0
    assert started == [workspace]


def test_command_line_theme_overrides_global_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    _write_config(home, "[console]\ntheme = \"console-light\"\n")

    from console.cli import build_parser

    config = load_global_config()
    assert build_parser(default_theme=config.theme).parse_args([]).theme == "console-light"
    assert (
        build_parser(default_theme=config.theme).parse_args(["--theme", "console-dark"]).theme
        == "console-dark"
    )
