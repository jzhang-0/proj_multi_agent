"""WS-010:仓库根 bus/ 迁移、回退,以及四个旧入口契约。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from bus.headless import main as hub_main
from bus.paths import BUS_DIRNAME, BusPaths
from console.cli import main as console_main
from roster.__main__ import USAGE
from roster.__main__ import main as roster_main
from workspace.cli import main as workspace_main
from workspace.errors import WorkspaceError
from workspace.migrate import MARKER, migrate
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store

ROOT = Path(__file__).resolve().parents[1]


def store_at(tmp_path: Path) -> Store:
    return Store(tmp_path / "amux-home")


def seed_legacy_bus(project: Path, name: str = "hello.json") -> Path:
    queue = project / BUS_DIRNAME / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    payload = queue / name
    payload.write_text(
        '{"to":"human","from":"claude","text":"hi","ts":"2026-08-17 00:00:00"}',
        encoding="utf-8",
    )
    return payload


def test_migrate_copies_and_keeps_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    project = tmp_path / "demo"
    project.mkdir()
    source = seed_legacy_bus(project)
    store = store_at(tmp_path)
    note = migrate(project, store=store)
    workspace = store.get_by_path(project)
    assert workspace is not None
    dest = workspace.state_dir / BUS_DIRNAME / "queue" / source.name
    assert dest.is_file()
    assert source.is_file()
    assert "可删源目录" in note
    assert "migrate --rollback" in note
    marker = (workspace.state_dir / BUS_DIRNAME / MARKER).read_text(encoding="utf-8")
    assert str(project / BUS_DIRNAME) in marker
    paths = BusPaths.resolve(cwd=project)
    assert paths.root == (workspace.state_dir / BUS_DIRNAME).resolve()
    assert paths.workspace == workspace.slug


def test_migrate_refuses_nonempty_dest_unless_forced(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed_legacy_bus(project)
    store = store_at(tmp_path)
    migrate(project, store=store)
    with pytest.raises(WorkspaceError, match="--force"):
        migrate(project, store=store)
    seed_legacy_bus(project, "second.json")
    migrate(project, store=store, force=True)
    workspace = store.get_by_path(project)
    assert (workspace.state_dir / BUS_DIRNAME / "queue" / "second.json").is_file()


def test_migrate_missing_legacy_bus_errors(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    with pytest.raises(WorkspaceError, match="没有仓库根"):
        migrate(project, store=store_at(tmp_path))


def test_rollback_copies_back_and_keeps_workspace_bus(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed_legacy_bus(project)
    store = store_at(tmp_path)
    migrate(project, store=store)
    workspace = store.get_by_path(project)
    dest_queue = workspace.state_dir / BUS_DIRNAME / "queue"
    (dest_queue / "after.json").write_text("{}", encoding="utf-8")
    note = migrate(project, store=store, rollback=True)
    legacy = project / BUS_DIRNAME / "queue" / "after.json"
    assert legacy.is_file()
    assert (dest_queue / "after.json").is_file()
    assert MARKER not in {p.name for p in (project / BUS_DIRNAME).iterdir()}
    assert "拷回" in note


def test_cli_migrate_and_rollback(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed_legacy_bus(project)
    store = store_at(tmp_path)
    out = io.StringIO()
    assert workspace_main(["migrate", str(project)], store=store, stdout=out) == 0
    assert "拷进工作区" in out.getvalue()
    out = io.StringIO()
    assert workspace_main(["migrate", "--rollback", str(project)], store=store, stdout=out) == 0
    assert "拷回" in out.getvalue()


def test_explicit_bus_root_still_wins_after_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux-home"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    project = tmp_path / "demo"
    project.mkdir()
    seed_legacy_bus(project)
    migrate(project, store=store_at(tmp_path))
    other = tmp_path / "other-bus"
    paths = BusPaths.resolve(other, cwd=project)
    assert paths.root == other.resolve()
    assert paths.workspace is None


def test_unregistered_cwd_still_falls_back_to_repo_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    stray = tmp_path / "stray"
    stray.mkdir()
    paths = BusPaths.resolve(cwd=stray)
    assert paths.root == (ROOT / BUS_DIRNAME).resolve()
    assert paths.workspace is None


def test_four_old_entries_keep_single_workspace_usage(tmp_path: Path) -> None:
    bus = tmp_path / "bus"
    assert console_main(["--headless", "--once", "--bus-root", str(bus)]) == 0
    assert hub_main(["--once", "--bus-root", str(bus)]) == 0
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert "python -m roster" in start
    assert "ROSTER" not in start
    hub = (ROOT / "hub.py").read_text(encoding="utf-8")
    assert "bus.headless" in hub
    assert len(hub.splitlines()) < 40
    assert "start.sh" in USAGE
    # roster 多一个多余参数仍打 v0 用法,不改语义
    assert roster_main(["up", "claude", "extra"]) == 1
    assert "roster up|down|restart" in USAGE
