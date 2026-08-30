"""WEB-003 §4 snapshot 端点的轻量回归：每端点一条正常路径 + 关键边界。

不铺全量组合；深入的 revision/epoch 语义与实时推送留给 WEB-004。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bus import BusPaths, Message, deposit
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from web.app import create_app
from web.auth import WebSession
from work.service import WorkService
from workspace.store import Store

PORT = 8787


def _client() -> TestClient:
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    client.get(f"/?token={session.token}")
    return client


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
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    resp = client.get("/api/v1/session")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_session_and_vocabulary_work_without_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unregistered(tmp_path, monkeypatch)
    client = _client()

    resp = client.get("/api/v1/session")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["actor"] == "human"
    assert body["capabilities"] == {
        "stream": False,
        "mirror": False,
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
    client = _client()

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


def test_full_snapshot_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bound_workspace(tmp_path, monkeypatch)
    client = _client()

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
