"""WS-003:总线按工作区隔离,显式 --bus-root / BUS_ROOT 优先。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bus import Message, deposit, pending, read_message
from bus.audit import AuditEvent, AuditLog
from bus.paths import ENV_BUS_ROOT, BusPaths
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    return root


def test_workspace_bus_is_under_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    monkeypatch.delenv(ENV_BUS_ROOT, raising=False)
    store = Store(home)
    project = _project(tmp_path, "alpha")
    ws, _ = store.add(project, slug="alpha")
    monkeypatch.chdir(project)
    paths = BusPaths.resolve().ensure()
    assert paths.workspace == "alpha"
    assert paths.root == (ws.state_dir / "bus").resolve()
    assert paths.queue == paths.root / "queue"


def test_bus_root_env_wins_over_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    Store(home).add(_project(tmp_path, "alpha"), slug="alpha")
    monkeypatch.chdir(tmp_path / "projects" / "alpha")
    override = tmp_path / "fromenv"
    monkeypatch.setenv(ENV_BUS_ROOT, str(override))
    paths = BusPaths.resolve()
    assert paths.root == override.resolve()
    assert paths.workspace is None


def test_explicit_root_wins_over_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    monkeypatch.setenv(ENV_BUS_ROOT, str(tmp_path / "fromenv"))
    Store(home).add(_project(tmp_path, "alpha"), slug="alpha")
    explicit = tmp_path / "explicit"
    paths = BusPaths.resolve(explicit)
    assert paths.root == explicit.resolve()
    assert paths.workspace is None


def test_two_workspaces_do_not_share_queues(tmp_path: Path) -> None:
    store = Store(tmp_path / "amux")
    wa, _ = store.add(_project(tmp_path, "alpha"), slug="alpha")
    wb, _ = store.add(_project(tmp_path, "beta"), slug="beta")
    paths_a = BusPaths.for_workspace(wa).ensure()
    paths_b = BusPaths.for_workspace(wb).ensure()
    deposit(Message.create("claude", "只给 alpha", sender="human"), paths_a)
    deposit(Message.create("codex", "只给 beta", sender="human"), paths_b)
    assert [read_message(path).text for path in pending(paths_a)] == ["只给 alpha"]
    assert [read_message(path).text for path in pending(paths_b)] == ["只给 beta"]
    assert paths_a.queue != paths_b.queue
    assert paths_a.log != paths_b.log
    assert paths_a.asks != paths_b.asks


def test_audit_records_workspace_slug(tmp_path: Path) -> None:
    ws, _ = Store(tmp_path / "amux").add(_project(tmp_path, "alpha"), slug="alpha")
    paths = BusPaths.for_workspace(ws).ensure()
    entry = AuditLog(paths).record(
        AuditEvent.DEPOSIT, Message.create("claude", "归属", sender="human")
    )
    assert entry["workspace"] == "alpha"
    assert '"workspace": "alpha"' in paths.log.read_text(encoding="utf-8")


def test_audit_omits_workspace_when_using_explicit_root(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    entry = AuditLog(paths).record(
        AuditEvent.DEPOSIT, Message.create("claude", "无归属", sender="human")
    )
    assert "workspace" not in entry
