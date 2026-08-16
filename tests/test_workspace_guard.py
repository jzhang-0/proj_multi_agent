"""WS-009:并发上限只告警、rm 关会话、孤儿会话回收。"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from roster.lifecycle import Lifecycle
from roster.schema import Member, Roster
from workspace.cleanup import (
    kill_workspace_sessions,
    orphan_sessions,
    reclaim_orphans,
)
from workspace.cli import main as workspace_main
from workspace.limits import Limits, warn_member_count, warn_workspace_count
from workspace.store import Store


@dataclass
class Pane:
    session_name: str


class FakeTmux:
    def __init__(self, sessions=()) -> None:
        self.sessions = set(sessions)
        self.killed: list[str] = []

    def list_panes(self, target=None, *, all_sessions: bool = False):
        return [Pane(name) for name in sorted(self.sessions)]

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def kill_session(self, name: str, missing_ok: bool = False) -> None:
        self.sessions.discard(name)
        self.killed.append(name)

    def new_session(self, name: str, **kwargs: object) -> None:
        self.sessions.add(name)


def store_at(tmp_path: Path) -> Store:
    return Store(tmp_path / "amux-home")


def make_project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "README").write_text("keep", encoding="utf-8")
    return root


def test_workspace_count_warns_but_still_adds(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    (store.home / "limits.toml").parent.mkdir(parents=True, exist_ok=True)
    store.ensure_home()
    (store.home / "limits.toml").write_text("warn_workspaces = 1\n", encoding="utf-8")
    store.add(make_project(tmp_path, "one"), slug="one")
    store.add(make_project(tmp_path, "two"), slug="two")
    warning = warn_workspace_count(store, limits=Limits.load(store.home))
    assert "超过建议上限 1" in warning
    assert "不拒绝" in warning
    assert {item.slug for item in store.list()} == {"one", "two"}


def test_zero_cap_disables_warning(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    store.ensure_home()
    (store.home / "limits.toml").write_text(
        "warn_workspaces = 0\nwarn_members = 0\n",
        encoding="utf-8",
    )
    store.add(make_project(tmp_path, "one"), slug="one")
    assert warn_workspace_count(store, limits=Limits.load(store.home)) == ""
    tmux = FakeTmux({"claude@one", "codex@one"})
    assert warn_member_count(tmux, limits=Limits.load(store.home)) == ""


def test_member_count_warns_but_still_starts() -> None:
    tmux = FakeTmux()
    roster = Roster(
        members=(Member(name="claude", command="true"),),
        source="test",
    )
    result = Lifecycle(roster, tmux, cwd=Path("/tmp")).up("claude")[0]
    assert result.changed is True
    assert "claude" in tmux.sessions
    tmux.sessions.update({"codex@demo", "agy@demo"})
    warning = warn_member_count(tmux, limits=Limits(warn_members=1))
    assert "超过建议上限 1" in warning
    assert "不拒绝" in warning


def test_rm_kills_namespaced_sessions_and_keeps_project_files(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = make_project(tmp_path, "alpha")
    store.add(project, slug="alpha")
    tmux = FakeTmux({"claude@alpha", "codex@alpha", "claude@beta"})
    killed = kill_workspace_sessions(tmux, "alpha")
    assert set(killed) == {"claude@alpha", "codex@alpha"}
    assert tmux.sessions == {"claude@beta"}
    out = io.StringIO()
    assert workspace_main(["rm", "alpha"], store=store, stdout=out) == 0
    assert "未改动项目文件" in out.getvalue()
    assert (project / "README").is_file()
    assert store.get("alpha") is None


def test_gc_reclaims_orphans_and_leaves_live_workspaces(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    store.add(make_project(tmp_path, "alpha"), slug="alpha")
    tmux = FakeTmux({"claude@alpha", "codex@gone", "agy@gone"})
    assert set(orphan_sessions(tmux, store)) == {"codex@gone", "agy@gone"}
    killed = reclaim_orphans(tmux, store)
    assert set(killed) == {"codex@gone", "agy@gone"}
    assert tmux.sessions == {"claude@alpha"}
