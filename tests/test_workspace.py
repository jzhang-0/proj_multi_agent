"""WS-001:工作区模型、slug、解析 API、amux.toml、四个子命令。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from console.cli import main as amux_main
from workspace import (
    ProjectConfig,
    SlugError,
    Store,
    WorkspaceNotFound,
    allocate_slug,
    load_project_config,
    require_from_cwd,
    resolve_from_cwd,
    suggested_slug,
    validate_slug,
)
from workspace.cli import main as workspace_main
from workspace.paths import ENV_AMUX_HOME, INDEX_NAME, META_NAME


def store_at(tmp_path: Path) -> Store:
    return Store(tmp_path / "amux-home")


def make_project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "README").write_text("keep me", encoding="utf-8")
    return root


def test_validate_slug_rejects_colon_and_dot() -> None:
    with pytest.raises(SlugError, match="session:window"):
        validate_slug("foo:bar")
    with pytest.raises(SlugError, match="静默改写成"):
        validate_slug("foo.bar")
    assert validate_slug("proj_multi_agent") == "proj_multi_agent"
    assert validate_slug("a-2") == "a-2"


def test_suggested_slug_asks_for_flag_when_dirname_illegal() -> None:
    with pytest.raises(SlugError, match="--slug"):
        suggested_slug("my.project")
    with pytest.raises(SlugError, match="--slug"):
        suggested_slug("项目")


def test_allocate_slug_adds_numeric_suffix() -> None:
    assert allocate_slug("demo", set()) == "demo"
    assert allocate_slug("demo", {"demo"}) == "demo-2"
    assert allocate_slug("demo", {"demo", "demo-2"}) == "demo-3"


def test_add_creates_layout_and_path_index(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = make_project(tmp_path, "alpha")
    ws, created = store.add(project)
    assert created is True
    assert ws.slug == "alpha"
    assert ws.project_root == project.resolve()
    assert (ws.state_dir / META_NAME).is_file()
    assert "path =" in (ws.state_dir / META_NAME).read_text(encoding="utf-8")
    index = store.path_index()
    assert index[project.resolve()].slug == "alpha"
    text = (store.home / INDEX_NAME).read_text(encoding="utf-8")
    assert str(project.resolve()) in text
    assert ' = "alpha"' in text or " = 'alpha'" in text


def test_add_same_path_is_idempotent(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = make_project(tmp_path, "alpha")
    first, created = store.add(project)
    second, created_again = store.add(project)
    assert created is True
    assert created_again is False
    assert first.slug == second.slug == "alpha"
    assert store.list() == [first]


def test_same_dirname_gets_numeric_suffix(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    one = tmp_path / "one" / "demo"
    two = tmp_path / "two" / "demo"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    first, _ = store.add(one)
    second, _ = store.add(two)
    assert first.slug == "demo"
    assert second.slug == "demo-2"
    assert {item.slug for item in store.list()} == {"demo", "demo-2"}


def test_explicit_slug_collision_errors(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    one = make_project(tmp_path, "one")
    two = make_project(tmp_path, "two")
    store.add(one, slug="taken")
    with pytest.raises(SlugError, match="占用"):
        store.add(two, slug="taken")


def test_add_does_not_write_project_side_files(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = make_project(tmp_path, "alpha")
    store.add(project)
    assert not (project / "amux.toml").exists()
    assert not (project / ".amux").exists()


def test_add_rejects_missing_directory(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    with pytest.raises(Exception, match="不是目录"):
        store.add(tmp_path / "nope")
    store = store_at(tmp_path)
    with pytest.raises(Exception, match="不是目录"):
        store.add(tmp_path / "nope")


def test_rm_deletes_state_not_project_files(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = make_project(tmp_path, "alpha")
    ws, _ = store.add(project)
    marker = project / "README"
    store.remove("alpha")
    assert not ws.state_dir.exists()
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert store.get("alpha") is None
    assert store.get_by_path(project) is None


def test_rm_missing_slug_errors(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    with pytest.raises(WorkspaceNotFound, match="没有叫"):
        store.remove("ghost")


def test_resolve_walks_up_and_prefers_nearest(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    outer = tmp_path / "outer"
    inner = outer / "inner"
    nested = inner / "src" / "pkg"
    nested.mkdir(parents=True)
    store.add(outer, slug="outer")
    store.add(inner, slug="inner")
    assert resolve_from_cwd(nested, store=store).slug == "inner"
    assert resolve_from_cwd(outer, store=store).slug == "outer"
    assert resolve_from_cwd(tmp_path, store=store) is None


def test_require_from_cwd_explains_how_to_add(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    with pytest.raises(WorkspaceNotFound, match="amux workspace add"):
        require_from_cwd(tmp_path, store=store)


def test_missing_amux_toml_uses_defaults(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    config = load_project_config(project)
    assert config == ProjectConfig()
    assert config.uses_defaults is True
    assert config.enabled is None
    assert dict(config.env) == {}


def test_amux_toml_loads_enabled_and_env(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    (project / "amux.toml").write_text(
        """
enabled = ["claude", "codex"]
[env]
FOO = "bar"
""",
        encoding="utf-8",
    )
    config = load_project_config(project)
    assert config.enabled == ("claude", "codex")
    assert config.env["FOO"] == "bar"
    assert config.uses_defaults is False


def test_amux_toml_rejects_unknown_fields(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    (project / "amux.toml").write_text("members = []\n", encoding="utf-8")
    with pytest.raises(Exception, match="未知字段"):
        load_project_config(project)


def test_cli_add_list_current_rm(tmp_path: Path) -> None:
    home = tmp_path / "amux-home"
    store = Store(home)
    project = make_project(tmp_path, "alpha")
    out = io.StringIO()
    err = io.StringIO()
    assert (
        workspace_main(
            ["add", str(project)],
            store=store,
            cwd=tmp_path,
            stdout=out,
            stderr=err,
        )
        == 0
    )
    assert "已登记工作区 alpha" in out.getvalue()
    out = io.StringIO()
    assert workspace_main(["list"], store=store, stdout=out, stderr=err) == 0
    assert "alpha" in out.getvalue()
    assert str(project.resolve()) in out.getvalue()
    out = io.StringIO()
    assert (
        workspace_main(
            ["current"],
            store=store,
            cwd=project,
            stdout=out,
            stderr=err,
        )
        == 0
    )
    assert out.getvalue().startswith("alpha ")
    out = io.StringIO()
    assert workspace_main(["rm", "alpha"], store=store, stdout=out, stderr=err) == 0
    assert "未改动项目文件" in out.getvalue()
    assert (project / "README").is_file()
    out = io.StringIO()
    err = io.StringIO()
    assert workspace_main(["list"], store=store, stdout=out, stderr=err) == 0
    assert "还没有工作区" in out.getvalue()


def test_cli_current_outside_workspace(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    err = io.StringIO()
    code = workspace_main(
        ["current"],
        store=store,
        cwd=tmp_path,
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 1
    assert "amux workspace add" in err.getvalue()


def test_amux_dispatches_workspace_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = make_project(tmp_path, "demo")
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    monkeypatch.chdir(project)
    assert amux_main(["workspace", "add"]) == 0
    listed = io.StringIO()
    assert workspace_main(["list"], store=Store(home), stdout=listed) == 0
    assert "demo" in listed.getvalue()


def test_amux_help_mentions_workspace() -> None:
    from console.cli import build_parser

    help_text = build_parser().format_help()
    assert "workspace add|list|rm|current" in help_text
