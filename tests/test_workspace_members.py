"""WS-004 / WS-005:成员落到项目根,amux msg 投进对应工作区总线。"""

from __future__ import annotations

import io
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from bus import pending, read_message
from bus.paths import ENV_BUS_ROOT, BusPaths
from console.cli import main as amux_main
from roster.lifecycle import Lifecycle
from roster.load import apply_overlay, load_effective_roster, load_roster
from roster.schema import Member, Roster, RosterError
from tmuxctl import Tmux
from workspace.config import ProjectConfig
from workspace.paths import ENV_AMUX_HOME
from workspace.session import bind_tmux
from workspace.store import Store


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "marker").write_text("project", encoding="utf-8")
    return root


def test_overlay_enables_subset_and_merges_env() -> None:
    roster = Roster(
        members=(
            Member(name="claude", command="cat", env={"A": "1", "B": "keep"}),
            Member(name="codex", command="cat"),
            Member(name="agy", command="cat", enabled=True),
        )
    )
    overlay = apply_overlay(
        roster,
        ProjectConfig(enabled=("claude", "codex"), env={"B": "proj", "C": "new"}),
    )
    assert [m.name for m in overlay.enabled_members()] == ["claude", "codex"]
    assert overlay.get("agy") is not None and overlay.get("agy").enabled is False
    claude = overlay.get("claude")
    assert claude is not None
    assert dict(claude.env) == {"A": "1", "B": "proj", "C": "new"}


def test_overlay_unknown_member_is_error() -> None:
    roster = Roster(members=(Member(name="claude", command="cat"),))
    with pytest.raises(RosterError, match="没有的成员"):
        apply_overlay(roster, ProjectConfig(enabled=("nope",)))


def test_effective_roster_reads_project_amux_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    project = _project(tmp_path, "alpha")
    Store(home).add(project, slug="alpha")
    (project / "amux.toml").write_text(
        'enabled = ["claude"]\n[env]\nWS = "1"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    roster = load_effective_roster()
    assert [m.name for m in roster.enabled_members()] == ["claude"]
    assert load_roster().get("claude") is not None
    assert dict(roster.get("claude").env)["WS"] == "1"


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    if shutil.which("tmux") is None:
        pytest.skip("没有 tmux")
    client = Tmux(socket_name=f"ws004-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_member_process_cwd_is_project_root_via_lsof(
    tmp_path: Path, isolated_tmux: Tmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("lsof") is None:
        pytest.skip("没有 lsof")
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    monkeypatch.delenv(ENV_BUS_ROOT, raising=False)
    project = _project(tmp_path, "alpha")
    Store(home).add(project, slug="alpha")
    monkeypatch.chdir(project)
    name = f"bot-{uuid.uuid4().hex[:6]}"
    member = Member(name=name, command="cat")
    bound = bind_tmux(isolated_tmux)
    life = Lifecycle(Roster(members=(member,)), bound)
    try:
        assert life.cwd == project.resolve()
        assert life.up(name)[0].changed
        pane = bound.list_panes(name)[0]
        proc = subprocess.run(
            ["lsof", "-a", "-p", str(pane.pane_pid), "-d", "cwd"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(project.resolve()) in proc.stdout
    finally:
        life.down(name)


def test_amux_msg_deposits_into_workspace_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "amux"
    monkeypatch.setenv(ENV_AMUX_HOME, str(home))
    monkeypatch.delenv(ENV_BUS_ROOT, raising=False)
    project = _project(tmp_path, "alpha")
    ws, _ = Store(home).add(project, slug="alpha")
    monkeypatch.chdir(project)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert amux_main(["msg", "claude", "来自项目目录"]) == 0
    paths = BusPaths.for_workspace(ws)
    queued = pending(paths)
    assert len(queued) == 1
    assert read_message(queued[0]).text == "来自项目目录"
    assert "已进入队列" in out.getvalue()
