"""WEB-008 成员管理 HTTP：服务端确认、认证 actor、临时收编与 Hub 静音。"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bus import BusPaths, Message, deposit
from bus.audit import AuditLog
from control.member_admin import ConfirmationStore, MemberAdminError
from tmuxctl import Tmux
from web.app import create_app
from web.auth import WebSession
from workspace.session import NamespacedTmux
from workspace.store import Store

PORT = 8787
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    tmux = Tmux(socket_name=f"web008-management-{uuid.uuid4().hex[:8]}")
    try:
        yield tmux
    finally:
        tmux.kill_server()


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux: Tmux,
    *,
    enabled: tuple[str, ...] = ("claude", "codex"),
):
    project = tmp_path / "project"
    project.mkdir()
    quoted = ", ".join(f'"{name}"' for name in enabled)
    (project / "amux.toml").write_text(f"enabled = [{quoted}]\n", encoding="utf-8")
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "web.context.bind_tmux",
        lambda **kwargs: NamespacedTmux(tmux, kwargs["names"]),
    )
    return workspace


def _app_client() -> tuple[object, WebSession]:
    session = WebSession.generate()
    return create_app(session=session, port=PORT), session


def _authenticate(client: TestClient, session: WebSession) -> dict[str, str]:
    client.get(f"/?token={session.token}")
    return {"X-Amux-Session": session.session_id}


def test_dangerous_down_requires_bound_one_time_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    workspace = _project(tmp_path, monkeypatch, isolated_tmux)
    isolated_tmux.new_session("claude@project", command="cat")
    isolated_tmux.new_session("codex@project", command="cat")
    app, session = _app_client()

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        headers = _authenticate(client, session)
        missing = client.post("/api/v1/members/claude/down", json={}, headers=headers)
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "confirmation-required"
        assert isolated_tmux.has_session("claude@project")

        issued = client.post(
            "/api/v1/members/claude/down/confirm", json={}, headers=headers
        ).json()
        token = issued["confirm_token"]

        mismatch = client.post(
            "/api/v1/members/codex/down",
            json={"confirm_token": token},
            headers=headers,
        )
        assert mismatch.status_code == 400
        assert isolated_tmux.has_session("codex@project")

        stopped = client.post(
            "/api/v1/members/claude/down",
            json={"confirm_token": token},
            headers=headers,
        )
        assert stopped.status_code == 200
        assert stopped.json()["action"] == "down"
        assert not isolated_tmux.has_session("claude@project")

        reused = client.post(
            "/api/v1/members/claude/down",
            json={"confirm_token": token},
            headers=headers,
        )
        assert reused.status_code == 400

    controls = [
        item
        for item in AuditLog(BusPaths.for_workspace(workspace)).entries()
        if item["event"] == "control"
    ]
    assert controls[-1]["from"] == "human"
    assert controls[-1]["action"] == "down"


def test_actor_is_never_accepted_from_control_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _project(tmp_path, monkeypatch, isolated_tmux, enabled=("claude",))
    isolated_tmux.new_session("claude@project", command="sh")
    app, session = _app_client()

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        headers = _authenticate(client, session)
        forged = client.post(
            "/api/v1/members/claude/interrupt",
            json={"actor": "root"},
            headers=headers,
        )
        assert forged.status_code == 400
        assert "认证上下文" in forged.json()["error"]["message"]

        actual = client.post(
            "/api/v1/members/claude/interrupt", json={}, headers=headers
        )
        assert actual.status_code == 200
        assert actual.json()["action"] == "interrupt"


def test_adopt_is_process_local_and_snapshot_marks_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _project(tmp_path, monkeypatch, isolated_tmux, enabled=("claude",))
    isolated_tmux.new_session("claude@project", command="cat")
    isolated_tmux.new_session("helper@project", command="cat")
    app, session = _app_client()

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        headers = _authenticate(client, session)
        listing = client.get("/api/v1/member-management").json()
        assert [item["name"] for item in listing["adoptable"]] == ["helper"]
        adopted = client.post(
            "/api/v1/members/adopt", json={"name": "helper"}, headers=headers
        )
        assert adopted.status_code == 200
        assert adopted.json()["temporary"] is True
        helper = next(
            item for item in client.get("/api/v1/members").json()["members"]
            if item["name"] == "helper"
        )
        assert helper["source"] == "adopted"

    replacement, replacement_session = _app_client()
    with TestClient(replacement, base_url=f"http://127.0.0.1:{PORT}") as client:
        _authenticate(client, replacement_session)
        assert "helper" not in {
            item["name"] for item in client.get("/api/v1/members").json()["members"]
        }


def test_mute_is_applied_by_hub_policy_and_remains_auditable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    workspace = _project(tmp_path, monkeypatch, isolated_tmux, enabled=("claude",))
    isolated_tmux.new_session("claude@project", command="cat")
    app, session = _app_client()
    paths = BusPaths.for_workspace(workspace).ensure()

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        headers = _authenticate(client, session)
        muted = client.post(
            "/api/v1/members/claude/mute", json={}, headers=headers
        )
        assert muted.json() == {"name": "claude", "muted": True}
        deposit(Message.create("human", "应被策略拒收", sender="claude"), paths)
        deadline = time.time() + 3
        while time.time() < deadline:
            rejected = [
                item for item in AuditLog(paths).entries() if item["event"] == "rejected"
            ]
            if rejected:
                break
            time.sleep(0.05)
        assert rejected[-1]["from"] == "claude"
        assert "静音" in rejected[-1]["reason"]


def test_confirmation_store_expires_and_consumes_once() -> None:
    now = [100.0]
    store = ConfirmationStore(ttl=30, clock=lambda: now[0])
    token, _ = store.issue("human", "claude", "terminate")
    with pytest.raises(MemberAdminError, match="不匹配"):
        store.consume(token, "other", "claude", "terminate")
    store.consume(token, "human", "claude", "terminate")
    with pytest.raises(MemberAdminError, match="无效或已使用"):
        store.consume(token, "human", "claude", "terminate")

    expired, _ = store.issue("human", "claude", "restart")
    now[0] = 131.0
    with pytest.raises(MemberAdminError, match="过期"):
        store.consume(expired, "human", "claude", "restart")


def test_every_member_write_endpoint_rejects_missing_double_submit_header_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _project(tmp_path, monkeypatch, isolated_tmux, enabled=("claude",))
    isolated_tmux.new_session("claude@project", command="cat")
    app, session = _app_client()
    requests = [
        ("POST", "/api/v1/members", {"name": "codex"}),
        ("DELETE", "/api/v1/members/claude", None),
        ("POST", "/api/v1/members/adopt", {"name": "helper"}),
        ("POST", "/api/v1/members/claude/mute", {}),
        ("POST", "/api/v1/members/claude/interrupt", {"actor": "forged"}),
        ("POST", "/api/v1/members/claude/terminate/confirm", {}),
        ("POST", "/api/v1/members/claude/terminate", {}),
        ("POST", "/api/v1/members/claude/restart/confirm", {}),
        ("POST", "/api/v1/members/claude/restart", {}),
        ("POST", "/api/v1/members/claude/down/confirm", {}),
        ("POST", "/api/v1/members/claude/down", {}),
        ("POST", "/api/v1/members/claude/up", {}),
        ("POST", "/api/v1/members/claude/attach", {}),
        ("POST", "/api/v1/members/claude/direct", {}),
    ]

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        for method, path, body in requests:
            response = client.request(method, path, json=body)
            assert response.status_code == 401, path
            assert response.json()["error"]["code"] == "unauthorized", path
