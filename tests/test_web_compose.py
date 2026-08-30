"""WEB-006：认证消息、ask/reply 与浏览器安全附件边界。"""

from __future__ import annotations

import contextlib
import json
import stat
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bus import BusPaths, Message, load_reply, pending, read_message, store_ask
from team.activation import roster_for_team
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from web.app import create_app
from web.auth import WebSession
from work.service import WorkService
from workspace.members import WorkspaceMembers, save_workspace_members
from workspace.store import Store

PORT = 8787


def _bound_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    team = teams.load(DEFAULT_TEAM_ID)
    roster = roster_for_team(team)
    save_workspace_members(
        workspace,
        WorkspaceMembers(
            names=tuple(member.name for member in roster.members),
            custom=roster.members,
            source=f"team:{team.id}",
        ),
    )
    bind_team(workspace, team.id, teams=teams)
    work = WorkService.for_workspace(workspace, teams=teams)
    work.create("fable", "浏览器消息闭环")
    work.assign("fable", "T-001", "sol")
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    return workspace


@contextlib.contextmanager
def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WebSession, object]]:
    workspace = _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        yield client, session, workspace


def _png(size: tuple[int, int] = (24, 16), color: str = "navy") -> bytes:
    payload = BytesIO()
    Image.new("RGB", size, color).save(payload, format="PNG")
    return payload.getvalue()


def _write_headers(session: WebSession) -> dict[str, str]:
    return {"X-Amux-Session": session.session_id}


def test_write_requires_double_submit_header_and_rejects_client_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as (client, session, workspace):
        paths = BusPaths.for_workspace(workspace)
        missing = client.post("/api/v1/messages", json={"text": "推进 T-016"})
        assert missing.status_code == 401
        assert pending(paths) == []

        forged = client.post(
            "/api/v1/messages",
            headers=_write_headers(session),
            json={"actor": "fable", "to": "sol", "text": "伪造身份"},
        )
        assert forged.status_code == 400
        assert forged.json()["error"]["code"] == "invalid-request"
        assert "actor" in forged.json()["error"]["message"]
        assert pending(paths) == []


def test_message_defaults_to_leader_links_task_and_uses_authenticated_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as (client, session, workspace):
        snapshot = client.get("/api/v1/session").json()
        assert snapshot["actor"] == "human"
        assert snapshot["write_token"] == session.session_id
        assert snapshot["capabilities"]["compose"] is True

        response = client.post(
            "/api/v1/messages",
            headers=_write_headers(session),
            json={"text": "请验收", "task_id": "T-001"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == {
            "id": response.json()["message"]["id"],
            "to": "fable",
            "kind": "message",
            "reply_to": None,
            "task_id": "T-001",
            "attachment_ids": [],
        }
        queued = read_message(pending(BusPaths.for_workspace(workspace))[0])
        assert queued.id
        assert (queued.sender, queued.to, queued.text, queued.task) == (
            "human",
            "fable",
            "请验收",
            "T-001",
        )


def test_ask_task_are_mutually_exclusive_and_reply_target_comes_from_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as (client, session, workspace):
        headers = _write_headers(session)
        conflict = client.post(
            "/api/v1/messages",
            headers=headers,
            json={"kind": "ask", "to": "sol", "text": "确认？", "task_id": "T-001"},
        )
        assert conflict.status_code == 400

        ask_response = client.post(
            "/api/v1/messages",
            headers=headers,
            json={"kind": "ask", "to": "sol", "text": "确认？"},
        )
        assert ask_response.status_code == 200
        ask_id = ask_response.json()["message"]["id"]
        assert (BusPaths.for_workspace(workspace).asks / f"{ask_id}.json").is_file()

        paths = BusPaths.for_workspace(workspace).ensure()
        incoming = Message.create("human", "可以合并吗？", sender="fable", kind="ask")
        store_ask(incoming, paths)
        reply = client.post(
            "/api/v1/messages",
            headers=headers,
            json={"kind": "reply", "reply_to": incoming.id, "text": "可以"},
        )
        assert reply.status_code == 200
        assert reply.json()["message"]["to"] == "fable"
        stored = load_reply(incoming.id or "", paths)
        assert stored is not None
        assert (stored.sender, stored.to, stored.text, stored.reply_to) == (
            "human",
            "fable",
            "可以",
            incoming.id,
        )

        forged_target = client.post(
            "/api/v1/messages",
            headers=headers,
            json={
                "kind": "reply",
                "reply_to": incoming.id,
                "to": "sol",
                "text": "再次回复",
            },
        )
        assert forged_target.status_code == 400


def test_upload_is_content_addressed_download_is_path_free_and_image_only_sends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as (client, session, workspace):
        headers = {**_write_headers(session), "Content-Type": "image/png"}
        first = client.post("/api/v1/attachments", headers=headers, content=_png())
        second = client.post("/api/v1/attachments", headers=headers, content=_png())

        assert first.status_code == 200
        assert second.json() == first.json()
        safe = first.json()["attachment"]
        assert set(safe) == {
            "id",
            "name",
            "media_type",
            "width",
            "height",
            "size",
            "download_url",
        }
        assert len(safe["id"]) == 16
        assert str(workspace.state_dir) not in json.dumps(first.json())
        stored = workspace.state_dir / "attachments" / f"clipboard-{safe['id']}.png"
        assert stored.is_file()
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600

        downloaded = client.get(safe["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert str(workspace.state_dir) not in str(downloaded.headers)

        sent = client.post(
            "/api/v1/messages",
            headers=_write_headers(session),
            json={"to": "sol", "attachment_ids": [safe["id"]]},
        )
        assert sent.status_code == 200
        queued = read_message(pending(BusPaths.for_workspace(workspace))[-1])
        assert queued.text == "请查看附加图片。"
        assert queued.attachments[0].path == str(stored)


def test_invalid_member_task_attachment_and_non_image_are_structured_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as (client, session, _workspace):
        headers = _write_headers(session)
        assert client.post(
            "/api/v1/messages", headers=headers, json={"to": "nobody", "text": "hi"}
        ).status_code == 404
        assert client.post(
            "/api/v1/messages", headers=headers, json={"text": "hi", "task_id": "bad"}
        ).status_code == 400
        assert client.post(
            "/api/v1/messages", headers=headers, json={"text": "hi", "task_id": "T-999"}
        ).status_code == 404
        missing_attachment = client.post(
            "/api/v1/messages",
            headers=headers,
            json={"to": "sol", "attachment_ids": ["0" * 16]},
        )
        assert missing_attachment.status_code == 404
        bad_upload = client.post(
            "/api/v1/attachments",
            headers={**headers, "Content-Type": "text/plain"},
            content=b"not an image",
        )
        assert bad_upload.status_code == 400
        assert bad_upload.headers["content-type"] == "application/json; charset=utf-8"
