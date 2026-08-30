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


def _direct_token(client: TestClient, session: WebSession, member: str = MEMBER) -> str:
    response = client.post(
        f"/api/v1/members/{member}/direct",
        json={},
        headers={"X-Amux-Session": session.session_id},
    )
    assert response.status_code == 200
    return response.json()["direct_token"]


def _acquire(client: TestClient, session: WebSession, *, force: bool = False) -> dict:
    return {
        "type": "lease",
        "action": "acquire",
        "force": force,
        "direct_token": _direct_token(client, session),
    }


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
            # 评审(opus)实测退回:①之前 captured_at 送的是 time.monotonic()
            # (进程运行秒数)，api-protocol §2.2 明令单调时钟不得出网，*_at
            # 必须是 epoch 秒；②帧缺 cols/rows，§5 canonical size 由租约
            # 持有者决定，非持有者只能靠帧里的这两个字段知道权威尺寸。
            assert frame_a["captured_at"] > 1_700_000_000  # 明显是 epoch 秒不是单调钟
            assert frame_a["cols"] > 0 and frame_a["rows"] > 0


def test_preemption_notifies_the_previous_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn_a, _ws(client, session) as conn_b:
            conn_a.send_json(_acquire(client, session))
            ack_a = _wait_for(conn_a, "lease_acquired")

            conn_b.send_json(_acquire(client, session))
            denied = _wait_for(conn_b, "lease_denied")
            assert denied["holder"]["owner"] == ack_a["holder"]["owner"]

            conn_b.send_json(_acquire(client, session, force=True))
            ack_b = _wait_for(conn_b, "lease_acquired")
            assert ack_b["preempted"] is True
            assert ack_b["previous_holder"]["owner"] == ack_a["holder"]["owner"]

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
            conn.send_json(_acquire(client, session))
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
            conn_a.send_json(_acquire(client, session))
            _wait_for(conn_a, "lease_acquired")

        # 连接已关闭(正常退出的 with 块)；下一个连接不带 force 也该拿到租约。
        with _ws(client, session) as conn_b:
            conn_b.send_json(_acquire(client, session))
            _wait_for(conn_b, "lease_acquired")


def test_rejects_bad_origin_with_explicit_close_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    """评审(opus)实测指出:`close()` 若在 `accept()` 之前调用，真实 ASGI 服务器
    会把它变成握手阶段的 HTTP 403，浏览器 JS 完全看不到状态码(`onclose` 只
    拿到 1006)。已改成先 `accept()` 再 `close(code=, reason=)`——真实
    uvicorn + `websockets` 客户端验证过这样能送达；这里用 TestClient 钉住
    同一行为:连接先被接受(`with` 正常进入)，第一次 `receive` 才看到关闭。
    """
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with client.websocket_connect(
            f"/api/v1/terminal/{MEMBER}/mirror",
            headers={"host": f"127.0.0.1:{PORT}", "origin": "http://evil.example"},
        ) as conn, pytest.raises(WebSocketDisconnect) as exc_info:
            conn.receive_json()
        assert exc_info.value.code == 4401
        assert exc_info.value.reason == "unauthorized"


def test_ws_handshake_rejections_log_which_check_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_tmux: Tmux,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BUG(T-025，human 实机复现):`unauthorized` 此前无从判断是 Host/Origin/
    cookie 哪一道校验没过——server 日志完全没有拒绝记录，human 与 opus 都
    定位不了。真实 uvicorn 场景已用真实 `websockets` 客户端手动验证过三条
    各自独立(见 T-025 证据)；这里用 TestClient 钉住"每种失败原因各自可从
    `caplog` 分辨"这条契约，防止以后又退化成一句笼统的 `unauthorized`。
    """
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)
    good_headers = {
        "host": f"127.0.0.1:{PORT}",
        "origin": f"http://127.0.0.1:{PORT}",
        "cookie": f"{COOKIE_NAME}={session.session_id}",
    }

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client, caplog.at_level("WARNING"):
        client.get(f"/?token={session.token}")

        caplog.clear()
        with client.websocket_connect(
            f"/api/v1/terminal/{MEMBER}/mirror",
            headers={**good_headers, "host": "evil.example.com:9999"},
        ) as conn, pytest.raises(WebSocketDisconnect):
            conn.receive_json()
        assert "check=host" in caplog.text

        caplog.clear()
        with client.websocket_connect(
            f"/api/v1/terminal/{MEMBER}/mirror",
            headers={**good_headers, "origin": "http://evil.example.com"},
        ) as conn, pytest.raises(WebSocketDisconnect):
            conn.receive_json()
        assert "check=origin" in caplog.text

        caplog.clear()
        with client.websocket_connect(
            f"/api/v1/terminal/{MEMBER}/mirror",
            headers={k: v for k, v in good_headers.items() if k != "cookie"},
        ) as conn, pytest.raises(WebSocketDisconnect):
            conn.receive_json()
        assert "check=cookie" in caplog.text


def test_ws_write_before_lease_acquire_logs_the_offending_message_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_tmux: Tmux,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BUG(T-025，根因):前端断线重连后曾经不清 `leaseHeldRef`，把
    `resize` 发给一条从没 acquire 过租约的新连接，被 §6.3"未持租约就关闭"
    的分支 4401——这里直接钉住服务端这半的契约(收到非白名单写消息且未持
    租约就关闭，并且日志里能看到具体是哪个 `msg_type`)，前端半的回归见
    `web/tests/e2e/terminal.spec.ts` 的重连用例。
    """
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client, caplog.at_level("WARNING"):
        client.get(f"/?token={session.token}")
        with _ws(client, session) as conn, pytest.raises(WebSocketDisconnect) as exc_info:
            conn.send_json({"type": "resize", "cols": 100, "rows": 30})
            conn.receive_json()
        assert exc_info.value.code == 4401
        assert "ws_write_reject" in caplog.text
        assert "reason=no-lease" in caplog.text
        assert "msg_type='resize'" in caplog.text


def test_mirror_write_upgrade_requires_fresh_direct_ticket_and_defaults_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_tmux: Tmux
) -> None:
    _bound_project(tmp_path, monkeypatch)
    isolated_tmux.new_session(SESSION_NAME, command="cat")
    app, session = _make_app(monkeypatch, isolated_tmux)

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        direct = _direct_token(client, session)
        with _ws(client, session) as first:
            # scroll 只改本连接的 history offset，是提权前唯一客户端只读帧。
            first.send_json({"type": "scroll", "offset": 20})
            first.send_json({
                "type": "lease",
                "action": "acquire",
                "force": False,
                "direct_token": direct,
            })
            _wait_for(first, "lease_acquired")
            first.close()

        with _ws(client, session) as reused:
            reused.send_json({
                "type": "lease",
                "action": "acquire",
                "force": False,
                "direct_token": direct,
            })
            with pytest.raises(WebSocketDisconnect) as duplicate:
                reused.receive_json()
        assert duplicate.value.code == 4401

        attach_ticket = client.post(
            f"/api/v1/members/{MEMBER}/attach",
            json={},
            headers={"X-Amux-Session": session.session_id},
        ).json()["attach_token"]
        with _ws(client, session) as wrong_action:
            wrong_action.send_json({
                "type": "lease",
                "action": "acquire",
                "force": False,
                "direct_token": attach_ticket,
            })
            with pytest.raises(WebSocketDisconnect) as mismatch:
                wrong_action.receive_json()
        assert mismatch.value.code == 4401

        with _ws(client, session) as unprivileged:
            unprivileged.send_json({"type": "resize", "cols": 100, "rows": 30})
            with pytest.raises(WebSocketDisconnect) as default_closed:
                unprivileged.receive_json()
        assert default_closed.value.code == 4401

        with _ws(client, session) as fake_lease_action:
            fake_lease_action.send_json({"type": "lease", "action": "release"})
            with pytest.raises(WebSocketDisconnect) as not_an_upgrade:
                fake_lease_action.receive_json()
        assert not_an_upgrade.value.code == 4401

        with _ws(client, session) as elevated:
            elevated.send_json(_acquire(client, session))
            _wait_for(elevated, "lease_acquired")
            elevated.send_json({"type": "future-write-frame", "payload": "ignored-before"})
            error = _wait_for(elevated, "error")
        assert error == {
            "type": "error",
            "code": "invalid-request",
            "message": "未知帧类型: 'future-write-frame'",
        }
