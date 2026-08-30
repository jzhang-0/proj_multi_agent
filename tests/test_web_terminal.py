"""WEB-007 后端半边(T-015)：镜像 WebSocket 通道回归。

用测试 WS 客户端覆盖派工描述的四个场景：TUI/Web 同时观看、抢占、回滚态拒绝
输入、断线释放。真实 tmux(隔离 socket，不碰系统默认 server)，没有 tmux 就
跳过——与 tests/test_console_members.py 的真实 tmux 集成测试同一约定。
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tmuxctl import Tmux
from web.app import create_app
from web.auth import COOKIE_NAME, WebSession
from workspace.session import NamespacedTmux
from workspace.store import Store

PORT = 8787
MEMBER = "claude"
SESSION_NAME = f"{MEMBER}@project"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="没有 tmux")


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"web007-{uuid.uuid4().hex[:8]}")
    try:
        yield client
    finally:
        client.kill_server()


def _bound_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "amux.toml").write_text(f'enabled = ["{MEMBER}"]\n', encoding="utf-8")
    store = Store(tmp_path / "amux-home")
    store.add(project, slug="project")
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)


def _make_app(monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux) -> tuple[FastAPI, WebSession]:
    """`web.context.bind_tmux` 换成隔离 socket 的 tmux，不碰系统默认 server。"""
    monkeypatch.setattr(
        "web.context.bind_tmux",
        lambda **kwargs: NamespacedTmux(isolated_tmux, kwargs.get("names")),
    )
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    app.state.heartbeat_interval = 0.05  # 别等生产的 5s 心跳周期
    return app, session


def _ws(client: TestClient, session: WebSession, member: str = MEMBER):
    # TestClient.websocket_connect 不像它的 HTTP 方法那样继承 base_url 的
    # Host 或 client 的 cookie jar(固定发 `Host: testserver`、不带 cookie)；
    # 三项鉴权都得在这里手动补上才能过 §6.3。
    return client.websocket_connect(
        f"/api/v1/terminal/{member}/mirror",
        headers={
            "host": f"127.0.0.1:{PORT}",
            "origin": f"http://127.0.0.1:{PORT}",
            "cookie": f"{COOKIE_NAME}={session.session_id}",
        },
    )


def _wait_for(ws, msg_type: str, *, attempts: int = 50, where=None):
    """镜像帧和离散回执共用一条连接，实时并发到达；等指定 `type`(可选再按
    `where` 过滤)，路上的 `frame`/`idle` 噪声跳过——这是协议的正常交织，
    不是缺陷。"""
    for _ in range(attempts):
        message = ws.receive_json()
        if message.get("type") == msg_type and (where is None or where(message)):
            return message
    raise AssertionError(f"未在 {attempts} 条消息内等到 type={msg_type!r}")


def test_concurrent_viewers_share_the_same_broadcast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        def has_text(message: dict) -> bool:
            return "hello-both" in message.get("data", "")

        with _ws(client, session) as tui_view, _ws(client, session) as web_view:
            isolated_tmux.send_keys(SESSION_NAME, "hello-both", literal=True)
            frame_a = _wait_for(tui_view, "frame", where=has_text)
            frame_b = _wait_for(web_view, "frame", where=has_text)
            assert frame_a["frame_seq"] == frame_b["frame_seq"]


def test_preemption_notifies_the_previous_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn_a, _ws(client, session) as conn_b:
            conn_a.send_json({"type": "lease", "action": "acquire", "force": False})
            ack_a = _wait_for(conn_a, "lease_acquired")

            conn_b.send_json({"type": "lease", "action": "acquire", "force": False})
            denied = _wait_for(conn_b, "lease_denied")
            assert denied["holder"]["owner"] == ack_a["holder"]["owner"]

            conn_b.send_json({"type": "lease", "action": "acquire", "force": True})
            _wait_for(conn_b, "lease_acquired")

            _wait_for(conn_a, "lease_lost")


def test_rollback_rejects_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn:
            conn.send_json({"type": "lease", "action": "acquire", "force": False})
            _wait_for(conn, "lease_acquired")

            conn.send_json({"type": "scroll", "offset": 50})

            conn.send_json({"type": "input", "kind": "key", "name": "Enter"})
            denied = _wait_for(conn, "denied")
            assert denied == {"type": "denied", "reason": "scrolled-back"}


def test_disconnect_releases_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn_a:
            conn_a.send_json({"type": "lease", "action": "acquire", "force": False})
            _wait_for(conn_a, "lease_acquired")

        # 连接已关闭(正常退出的 with 块)；下一个连接不带 force 也该拿到租约。
        with _ws(client, session) as conn_b:
            conn_b.send_json({"type": "lease", "action": "acquire", "force": False})
            _wait_for(conn_b, "lease_acquired")


def test_rejects_bad_origin_without_accepting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(
                f"/api/v1/terminal/{MEMBER}/mirror",
                headers={"host": f"127.0.0.1:{PORT}", "origin": "http://evil.example"},
            ),
        ):
            pass
        assert exc_info.value.code == 4401
