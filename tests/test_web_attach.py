"""WEB-008 完整接管：真实 tmux PTY、抢占回收与 SIGKILL 尺寸恢复。"""

from __future__ import annotations

import os
import shutil
import signal
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bus import BusPaths
from bus.audit import AuditLog
from tmuxctl import Tmux, TmuxAttachProcess, WindowSizeGuard
from web.app import create_app
from web.auth import COOKIE_NAME, WebSession
from workspace.session import NamespacedTmux
from workspace.store import Store

PORT = 8787
MEMBER = "claude"
SESSION = "claude@project"
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    tmux = Tmux(socket_name=f"web008-attach-{uuid.uuid4().hex[:8]}")
    try:
        yield tmux
    finally:
        tmux.kill_server()


def _bound_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmux: Tmux):
    project = tmp_path / "project"
    project.mkdir()
    (project / "amux.toml").write_text('enabled = ["claude"]\n', encoding="utf-8")
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "web.context.bind_tmux",
        lambda **kwargs: NamespacedTmux(tmux, kwargs["names"]),
    )
    return workspace


def _ws(client: TestClient, session: WebSession, member: str = MEMBER):
    return client.websocket_connect(
        f"/api/v1/terminal/{member}/attach",
        headers={
            "host": f"127.0.0.1:{PORT}",
            "origin": f"http://127.0.0.1:{PORT}",
            "cookie": f"{COOKIE_NAME}={session.session_id}",
        },
    )


def _wait_lease_released(app) -> None:
    deadline = time.time() + 3
    while time.time() < deadline:
        if app.state.lease_manager.holder(MEMBER) is None:
            return
        time.sleep(0.02)
    assert app.state.lease_manager.holder(MEMBER) is None


def _authorize(client: TestClient, session: WebSession) -> str:
    response = client.post(
        f"/api/v1/members/{MEMBER}/attach",
        json={},
        headers={"X-Amux-Session": session.session_id},
    )
    assert response.status_code == 200
    return response.json()["attach_token"]


def test_attach_disconnect_reaps_client_releases_lease_and_keeps_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    workspace = _bound_project(tmp_path, monkeypatch, isolated_tmux)
    isolated_tmux.new_session(SESSION, command="cat")
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    app.state.heartbeat_interval = 0.05

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn:
            conn.send_json({
                "type": "attach",
                "attach_token": _authorize(client, session),
                "force": False,
                "cols": 100,
                "rows": 30,
            })
            attached = conn.receive_json()
            assert attached["type"] == "attached"
            conn.send_bytes(b"hello from pty\r")
            conn.close()
            assert conn.receive()["type"] == "websocket.close"
        _wait_lease_released(app)

    assert isolated_tmux.has_session(SESSION)
    takeover = [
        item
        for item in AuditLog(BusPaths.for_workspace(workspace)).entries()
        if item.get("action") == "takeover"
    ]
    assert [item["changed"] for item in takeover] == [True, False]
    assert "断线释放" in takeover[-1]["reason"]


def test_force_preemption_finishes_old_pty_before_new_attach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch, isolated_tmux)
    isolated_tmux.new_session(SESSION, command="cat")
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    app.state.heartbeat_interval = 0.05

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as first:
            first.send_json({
                "type": "attach",
                "attach_token": _authorize(client, session),
                "force": False,
                "cols": 90,
                "rows": 24,
            })
            assert first.receive_json()["type"] == "attached"
            with _ws(client, session) as second:
                second.send_json({
                    "type": "attach",
                    "attach_token": _authorize(client, session),
                    "force": True,
                    "cols": 110,
                    "rows": 32,
                })
                assert second.receive_json()["type"] == "attached"
                second.send_json({"type": "exit"})
                assert second.receive()["type"] == "websocket.close"
            assert first.receive()["type"] == "websocket.close"
        _wait_lease_released(app)
    assert isolated_tmux.has_session(SESSION)


def test_tmux_attach_process_uses_fixed_exact_argv_and_waitpid(isolated_tmux: Tmux) -> None:
    isolated_tmux.new_session("standalone", command="cat")
    assert isolated_tmux.attach_argv("standalone")[-3:] == [
        "attach-session",
        "-t",
        "=standalone",
    ]
    process = TmuxAttachProcess.spawn(
        isolated_tmux,
        "standalone",
        cols=80,
        rows=24,
        environ={"PATH": os.environ["PATH"], "WEB_SECRET": "must-not-pass"},
    )
    pid = process.pid
    assert process.close() <= 128
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    assert isolated_tmux.has_session("standalone")


def test_attach_ticket_is_single_use_and_bound_to_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "amux.toml").write_text(
        'enabled = ["claude", "codex"]\n', encoding="utf-8"
    )
    store = Store(tmp_path / "amux-home")
    store.add(project, slug="project")
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "web.context.bind_tmux",
        lambda **kwargs: NamespacedTmux(isolated_tmux, kwargs["names"]),
    )
    isolated_tmux.new_session(SESSION, command="cat")
    isolated_tmux.new_session("codex@project", command="cat")
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        ticket = _authorize(client, session)
        assert len(ticket) >= 32  # token_urlsafe(24):192 bit CSPRNG output
        with _ws(client, session, "codex") as wrong_member:
            wrong_member.send_json({
                "type": "attach",
                "attach_token": ticket,
                "force": False,
                "cols": 80,
                "rows": 24,
            })
            mismatch = wrong_member.receive()
        assert mismatch["type"] == "websocket.close"
        assert mismatch["code"] == 4401
        assert mismatch["reason"] == "unauthorized"

        with _ws(client, session) as valid:
            valid.send_json({
                "type": "attach",
                "attach_token": ticket,
                "force": False,
                "cols": 80,
                "rows": 24,
            })
            assert valid.receive_json()["type"] == "attached"
            valid.send_json({"type": "exit"})
            assert valid.receive()["type"] == "websocket.close"

        with _ws(client, session) as reused:
            reused.send_json({
                "type": "attach",
                "attach_token": ticket,
                "force": False,
                "cols": 80,
                "rows": 24,
            })
            duplicate = reused.receive()
        assert duplicate["type"] == "websocket.close"
        assert duplicate["code"] == 4401


def test_attach_ticket_expiry_and_missing_first_frame_are_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch, isolated_tmux)
    isolated_tmux.new_session(SESSION, command="cat")
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        now = [100.0]
        app.state.member_admin.attach_tokens._clock = lambda: now[0]
        ticket = _authorize(client, session)
        now[0] = 131.0
        with _ws(client, session) as expired:
            expired.send_json({
                "type": "attach",
                "attach_token": ticket,
                "force": False,
                "cols": 80,
                "rows": 24,
            })
            rejected = expired.receive()
        assert rejected["type"] == "websocket.close"
        assert rejected["code"] == 4401
        assert rejected["reason"] == "unauthorized"


def test_window_size_guard_recovers_manual_option_after_owner_sigkill(
    isolated_tmux: Tmux,
) -> None:
    isolated_tmux.new_session("guarded", command="cat")
    ready_r, ready_w = os.pipe()
    owner = os.fork()
    if owner == 0:  # pragma: no cover - child deliberately receives SIGKILL
        os.close(ready_r)
        guard = WindowSizeGuard()
        guard.track(isolated_tmux, "guarded", identity="web-process")
        isolated_tmux.fit_window("guarded", 93, 27)
        os.write(ready_w, b"1")
        os.close(ready_w)
        signal.pause()
        os._exit(0)

    os.close(ready_w)
    assert os.read(ready_r, 1) == b"1"
    os.close(ready_r)
    assert isolated_tmux.show_window_option("guarded", "window-size") == "manual"
    os.kill(owner, signal.SIGKILL)
    os.waitpid(owner, 0)

    deadline = time.time() + 3
    while time.time() < deadline:
        if isolated_tmux.show_window_option("guarded", "window-size") is None:
            break
        time.sleep(0.05)
    assert isolated_tmux.show_window_option("guarded", "window-size") is None
