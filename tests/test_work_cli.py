"""TEAM-002：amux task 命令入口与可回看的账本输出。"""

from __future__ import annotations

import io
from pathlib import Path

from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from work.cli import main as task_main
from workspace.store import Store


def _context(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    return project, store, teams


def _run(project, store, teams, actor, *args):
    out, err = io.StringIO(), io.StringIO()
    code = task_main(
        list(args),
        actor=actor,
        store=store,
        teams=teams,
        cwd=project,
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_cli_records_and_renders_a_complete_flow(tmp_path: Path) -> None:
    project, store, teams = _context(tmp_path)
    assert _run(project, store, teams, "fable", "create", "修复登录页")[0] == 0
    assert _run(project, store, teams, "fable", "assign", "T-001", "sonnet")[0] == 0
    assert (
        _run(
            project,
            store,
            teams,
            "sonnet",
            "evidence",
            "T-001",
            "tests/test_login.py · 4 passed",
        )[0]
        == 0
    )
    assert _run(project, store, teams, "sonnet", "submit", "T-001", "实现完成")[0] == 0
    assert _run(project, store, teams, "fable", "accept", "T-001", "证据可信")[0] == 0
    assert _run(project, store, teams, "fable", "report", "T-001", "已汇报")[0] == 0

    code, board, err = _run(project, store, teams, "human", "list")
    assert code == 0 and err == ""
    assert "T-001" in board and "已完成" in board and "sonnet" in board
    code, detail, err = _run(project, store, teams, "human", "show", "T-001")
    assert code == 0 and err == ""
    for text in ("Leader: fable", "tests/test_login.py", "Leader 验收", "向 human 汇报"):
        assert text in detail


def test_cli_rejects_human_mutation_but_allows_reading(tmp_path: Path) -> None:
    project, store, teams = _context(tmp_path)
    code, _out, err = _run(project, store, teams, "human", "create", "绕过 Leader")
    assert code == 1
    assert "只有团队 Leader fable" in err
    assert _run(project, store, teams, "human", "list")[:2] == (
        0,
        "当前筛选下没有任务。\n",
    )


def test_cli_requires_a_bound_team(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    store.add(project, slug="project")
    out, err = io.StringIO(), io.StringIO()
    code = task_main(
        ["list"],
        store=store,
        cwd=project,
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert "尚未绑定团队" in err.getvalue()
