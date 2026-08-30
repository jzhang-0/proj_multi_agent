"""WEB-003 §4 snapshot 端点的轻量回归：每端点一条正常路径 + 关键边界。

不铺全量组合；深入的 revision/epoch 语义与实时推送留给 WEB-004。

`create_app()` 现在带 `lifespan`(常驻 `MemberStatusService`)，所以这里一律
用 `with TestClient(...) as client:`——不用 `with` 时 ASGI lifespan 不会
触发，snapshot API 应返回结构化 503。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus import BusPaths, Message, deposit
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from tmuxctl.errors import TmuxNotFoundError
from web.app import create_app
from web.auth import WebSession
from work.service import WorkService
from workspace.store import Store

PORT = 8787


def _make_app() -> tuple[FastAPI, WebSession]:
    session = WebSession.generate()
    return create_app(session=session, port=PORT), session


@contextlib.contextmanager
def _client() -> Iterator[TestClient]:
    app, session = _make_app()
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        yield client


def _unregistered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMUX_HOME", str(tmp_path / "amux-home"))
    monkeypatch.chdir(tmp_path)


def _bound_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "amux.toml").write_text('enabled = ["claude", "codex"]\n', encoding="utf-8")
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    service = WorkService.for_workspace(workspace, teams=teams)
    service.create("fable", "登录页修复")
    service.assign("fable", "T-001", "sonnet")
    service.progress("sonnet", "T-001", "已定位回归")
    service.add_evidence("sonnet", "T-001", "tests/test_login.py · 4 passed")
    service.submit("sonnet", "T-001", "实现与测试已提交")
    paths = BusPaths.for_workspace(workspace).ensure()
    deposit(Message.create("sonnet", "请确认登录页边界", sender="fable", task="T-001"), paths)
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    return workspace


def test_api_requires_session_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _unregistered(tmp_path, monkeypatch)
    app, _session = _make_app()
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        resp = client.get("/api/v1/session")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_snapshot_api_without_lifespan_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未跑 lifespan 时认证仍优先，认证后所有 snapshot 路由统一按 §2.4 降级。"""
    _unregistered(tmp_path, monkeypatch)
    app, session = _make_app()
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    try:
        assert client.get("/api/v1/members").status_code == 401
        client.get(f"/?token={session.token}")
        for path in ("/api/v1/workspace", "/api/v1/members"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "work-unavailable"
    finally:
        client.close()


def test_bad_host_error_has_charset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """opus 复审实测发现坏 Host 的 401 缺 `charset=utf-8`(§2.1)；这里钉住。"""
    _unregistered(tmp_path, monkeypatch)
    app, _session = _make_app()
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        resp = client.get("/api/v1/session", headers={"host": "evil.example:1234"})
        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json; charset=utf-8"


def test_session_and_vocabulary_work_without_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unregistered(tmp_path, monkeypatch)
    with _client() as client:
        resp = client.get("/api/v1/session")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json; charset=utf-8"
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["actor"] == "human"
        assert body["capabilities"] == {
            "stream": True,
            "mirror": True,
            "compose": False,
            "control": False,
        }

        vocab = client.get("/api/v1/vocabulary")
        assert vocab.status_code == 200
        assert "cache-control" not in vocab.headers or vocab.headers["cache-control"] != "no-store"
        assert any(item["value"] == "in-progress" for item in vocab.json()["task_status"])


def test_workspace_and_work_report_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unregistered(tmp_path, monkeypatch)
    with _client() as client:
        ws = client.get("/api/v1/workspace")
        assert ws.status_code == 200
        assert ws.json()["registered"] is False
        assert ws.json()["slug"] is None

        team = client.get("/api/v1/team")
        assert team.status_code == 200
        assert team.json()["bound"] is False

        work = client.get("/api/v1/work")
        assert work.status_code == 409
        assert work.json()["error"]["code"] == "workspace-unregistered"


def test_bound_but_empty_ledger_reports_empty_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "amux.toml").write_text("enabled = []\n", encoding="utf-8")
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)

    with _client() as client:
        work = client.get("/api/v1/work").json()
        assert work["summary"]["total"] == 0
        assert work["tasks"] == []
        assert work["selected_default"] is None

        timeline = client.get("/api/v1/timeline").json()
        assert timeline["entries"] == []
        assert timeline["head_seq"] == 0
        assert timeline["oldest_seq"] is None
        assert timeline["has_more"] is False

        members = client.get("/api/v1/members").json()
        assert members["members"] == []

        health = client.get("/api/v1/health").json()
        assert isinstance(health["degraded"], bool)


def test_full_snapshot_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bound_workspace(tmp_path, monkeypatch)
    with _client() as client:
        ws = client.get("/api/v1/workspace").json()
        assert ws["registered"] is True
        assert ws["slug"] == "project"

        team = client.get("/api/v1/team").json()
        assert team["bound"] is True
        assert team["leader"] == "fable"
        assert all("command" not in member and "env" not in member for member in team["members"])

        work = client.get("/api/v1/work").json()
        assert work["summary"]["total"] == 1
        assert work["tasks"][0]["id"] == "T-001"
        assert work["tasks"][0]["status"] == "submitted"

        detail = client.get("/api/v1/work/tasks/T-001")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["task"]["id"] == "T-001"
        assert len(detail_body["communications"]) == 1
        assert detail_body["communications"][0]["sender"] == "fable"

        missing = client.get("/api/v1/work/tasks/T-999")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not-found"
        assert missing.headers["content-type"] == "application/json; charset=utf-8"

        bad_id = client.get("/api/v1/work/tasks/not-an-id")
        assert bad_id.status_code == 400
        assert bad_id.json()["error"]["code"] == "invalid-request"

        timeline = client.get("/api/v1/timeline").json()
        assert timeline["head_seq"] >= 1
        assert any(entry["task_id"] == "T-001" for entry in timeline["entries"])

        filtered = client.get("/api/v1/timeline", params={"category": "task"}).json()
        assert all(entry["category"] == "task" for entry in filtered["entries"])

        missing_body = client.get("/api/v1/timeline/999999/body")
        assert missing_body.status_code == 404

        members = client.get("/api/v1/members").json()
        assert sorted(item["name"] for item in members["members"]) == ["claude", "codex"]

        health = client.get("/api/v1/health").json()
        assert isinstance(health["degraded"], bool)

        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["work"]["tasks"][0]["id"] == "T-001"


def test_members_reach_working_after_feeding_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评审(opus)实测：每请求新建 MemberStatusService 时 working/stuck 不可达、
    silent_for 恒 None。现在 `/api/v1/members` 读常驻服务；直接喂一次输出，
    验证 web 路径读到的状态和 TUI 会读到的(`member_status.snapshot(name)`)一致。
    """
    _bound_workspace(tmp_path, monkeypatch)
    app, session = _make_app()
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")

        app.state.member_status.record_output("claude", b"working on it...")
        tui_equivalent = app.state.member_status.snapshot("claude")

        resp = client.get("/api/v1/members")
        assert resp.status_code == 200
        web_entry = next(item for item in resp.json()["members"] if item["name"] == "claude")

        assert web_entry["state"] == "working"
        assert web_entry["state"] == tui_equivalent.state
        assert isinstance(web_entry["silent_for"], float)
        assert web_entry["silent_for"] == pytest.approx(tui_equivalent.silent_for, abs=0.5)


def test_members_and_health_survive_missing_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tmux 不可用时 lifespan 不能让应用起不来，端点也不能因此抛错(`MemberStatusService.run`
    在 `tmux is None` 时直接返回，见 `control/members.py`)。"""
    workspace = _bound_workspace(tmp_path, monkeypatch)

    def _no_tmux(*_args: object, **_kwargs: object):
        raise TmuxNotFoundError("tmux 不存在(测试模拟)")

    monkeypatch.setattr("web.context.bind_tmux", _no_tmux)

    with _client() as client:
        members = client.get("/api/v1/members")
        assert members.status_code == 200
        member_rows = members.json()["members"]
        assert sorted(item["name"] for item in member_rows) == ["claude", "codex"]
        assert all(item["alive"] is False for item in member_rows)
        assert all(item["state"] == "dead" for item in member_rows)

        (workspace.project_root / "amux.toml").write_text(
            'enabled = ["claude", "codex", "cursor"]\n', encoding="utf-8"
        )
        updated_rows = client.get("/api/v1/members").json()["members"]
        assert sorted(item["name"] for item in updated_rows) == ["claude", "codex", "cursor"]
        assert all(item["alive"] is False for item in updated_rows)
        assert all(item["state"] == "dead" for item in updated_rows)

        health = client.get("/api/v1/health")
        assert health.status_code == 200
