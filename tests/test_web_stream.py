"""WEB-004: versioned WebSocket 事件流与慢客户端有界队列。

不拉 SPA；用 TestClient 当协议客户端。文件变更通过 EventHub.scan_* 同步
触发，避免测试绑死 watchfiles 时序。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bus import BusPaths, Message, deposit
from control.health import Fault, FaultKind, HealthMonitor
from control.members import MemberStatusService
from control.timeline import TimelineEntry
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from tmuxctl.client import PaneInfo
from web.app import create_app
from web.auth import COOKIE_NAME, WebSession
from web.state import RevisionTracker, TimelineCache, timeline_revision_fingerprint
from web.stream import (
    CLOSE_NOT_FOUND,
    CLOSE_SLOW,
    CLOSE_UNAUTHORIZED,
    CLOSE_UNAVAILABLE,
    DeltaRing,
    EventHub,
    StreamClient,
    StreamSettings,
    _timeline_ops,
)
from work.service import WorkService
from workspace.store import Store

PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"
WS_HEADERS = {"Origin": ORIGIN, "Host": f"127.0.0.1:{PORT}"}
FAST = StreamSettings(
    ping_interval_s=3600.0,
    idle_timeout_s=3600.0,
    member_interval_s=3600.0,
    health_interval_s=3600.0,
)


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
    paths = BusPaths.for_workspace(workspace).ensure()
    deposit(Message.create("sonnet", "请确认边界", sender="fable", task="T-001"), paths)
    monkeypatch.setenv("AMUX_HOME", str(store.home))
    monkeypatch.chdir(project)
    return workspace, service, paths


def _must_reject(
    client: TestClient, headers: dict[str, str], *, code: int = CLOSE_UNAUTHORIZED
) -> None:
    with (
        client.websocket_connect("/api/v1/stream", headers=headers) as ws,
        pytest.raises(WebSocketDisconnect) as rejected,
    ):
        ws.receive_json()
    assert rejected.value.code == code


def _ws(client: TestClient):
    headers = dict(WS_HEADERS)
    cookie = client.cookies.get(COOKIE_NAME)
    if cookie:
        headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
    return client.websocket_connect("/api/v1/stream", headers=headers)


def _freeze_work_clock(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    """让随后的 work 事件使用指定 UTC 时间，从而控制它在时间线里的插入位置。"""

    class FrozenDateTime:
        @staticmethod
        def now(tz: object | None = None) -> datetime:
            if tz is None:
                return when
            return when.astimezone(tz)  # type: ignore[arg-type]

    monkeypatch.setattr("work.ledger.datetime", FrozenDateTime)


def test_overflow_merges_domain_then_global_then_closes() -> None:
    client = StreamClient(queue_max=2)
    epoch = "abc"
    frame = {"type": "delta", "epoch": epoch, "domain": "timeline"}
    assert client.enqueue({**frame, "revision": 1}) is None
    assert client.enqueue({**frame, "revision": 2}) is None
    assert client.enqueue({**frame, "revision": 3}) is None
    assert list(client._pending) == [
        {"type": "resync", "epoch": epoch, "domain": "timeline", "reason": "overflow"}
    ]

    stuffed = StreamClient(queue_max=1)
    stuffed.enqueue({"type": "invalidation", "epoch": epoch, "domain": "member", "revision": 1})
    stuffed.enqueue({"type": "invalidation", "epoch": epoch, "domain": "team", "revision": 1})
    assert list(stuffed._pending) == [
        {"type": "resync", "epoch": epoch, "domain": "*", "reason": "overflow"}
    ]
    assert stuffed.enqueue({"type": "delta", "epoch": epoch, "domain": "work", "revision": 1}) == (
        CLOSE_SLOW
    )

    zero = StreamClient(queue_max=0)
    closed = zero.enqueue({"type": "delta", "epoch": epoch, "domain": "work", "revision": 1})
    assert closed == CLOSE_SLOW


def test_first_workspace_scan_does_not_emit_epoch_changed() -> None:
    """首扫不得 reset_epoch；订阅客户端队列里不能出现 epoch_changed。"""
    tracker = RevisionTracker()
    epoch = tracker.epoch
    hub = EventHub(
        tracker=tracker,
        cache=TimelineCache(),
        member_status=MemberStatusService(()),
        health=None,
        tmux=None,
        settings=FAST,
    )
    client = hub.add_client()
    hub.scan_files_now()
    assert tracker.epoch == epoch
    assert all(frame.get("type") != "epoch_changed" for frame in client._pending)
    hub.scan_files_now()
    assert tracker.epoch == epoch
    assert all(frame.get("type") != "epoch_changed" for frame in client._pending)


def test_overflow_third_level_closes_unread_client() -> None:
    client = StreamClient(queue_max=4)
    epoch = "abc"
    closed: int | None = None
    for i in range(400):
        domain = "timeline" if i % 2 == 0 else "work"
        closed = client.enqueue(
            {"type": "delta", "epoch": epoch, "domain": domain, "revision": i}
        )
        if closed is not None:
            break
    else:
        raise AssertionError("400 帧仍未关闭")
    assert closed == CLOSE_SLOW
    assert client.close_code == CLOSE_SLOW


def test_delta_ring_replays_contiguous_and_reports_gap() -> None:
    ring = DeltaRing(2)
    ring.append(1, {"revision": 1})
    ring.append(2, {"revision": 2})
    ring.append(3, {"revision": 3})
    assert ring.replay(2, 3) == [{"revision": 3}]
    assert ring.replay(0, 3) is None
    assert ring.replay(3, 3) == []


def test_timeline_ops_resyncs_on_same_second_bus_insert() -> None:
    """opus 实测：秒粒度总线 ts 插到任务事件前，按 seq 槽 diff 会错位。"""
    old = {
        1: {
            "seq": 1,
            "key": "work:660023f",
            "outcome": "shown",
            "reason": "",
            "text": "建立·任务甲",
        }
    }
    new = [
        {"seq": 1, "key": "c35adc11", "outcome": "pending", "reason": "", "text": "消息一"},
        {
            "seq": 2,
            "key": "work:660023f",
            "outcome": "shown",
            "reason": "",
            "text": "建立·任务甲",
        },
    ]
    assert _timeline_ops(old, new) is None


def test_timeline_ops_resyncs_when_existing_key_changes_seq() -> None:
    old = {2: {"seq": 2, "key": "bus:x", "outcome": "ok", "reason": ""}}
    new = [
        {"seq": 2, "key": "work:1", "outcome": "shown", "reason": ""},
        {"seq": 3, "key": "bus:x", "outcome": "ok", "reason": ""},
    ]
    assert _timeline_ops(old, new) is None


def test_timeline_ops_append_and_update_when_seq_stable() -> None:
    old = {1: {"seq": 1, "key": "bus:x", "outcome": "pending", "reason": ""}}
    appended = {"seq": 2, "key": "bus:y", "outcome": "pending", "reason": ""}
    assert _timeline_ops(
        old,
        [
            {"seq": 1, "key": "bus:x", "outcome": "pending", "reason": ""},
            appended,
        ],
    ) == [{"op": "append", "entry": appended}]
    assert _timeline_ops(
        old, [{"seq": 1, "key": "bus:x", "outcome": "delivered", "reason": ""}]
    ) == [{"op": "update", "seq": 1, "outcome": "delivered", "reason": ""}]


def test_timeline_revision_fingerprint_detects_middle_work_event() -> None:
    last = TimelineEntry("now", "fable", "sonnet", "末条", key="bus:last")
    inserted = TimelineEntry("old", "sonnet", "fable", "进展", key="work:mid")
    before = timeline_revision_fingerprint(None, None, [last])
    after = timeline_revision_fingerprint(None, None, [inserted, last])
    assert before != after
    assert before[3:] == after[3:]


def test_stream_rejects_missing_cookie_and_bad_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        _must_reject(client, WS_HEADERS)
        client.get(f"/?token={session.token}")
        _must_reject(
            client,
            {"Origin": "http://evil.example", "Host": f"127.0.0.1:{PORT}"},
        )


def test_stream_handshake_close_codes_align_with_terminal() -> None:
    assert CLOSE_UNAUTHORIZED == 4401
    assert CLOSE_NOT_FOUND == 4404
    assert CLOSE_UNAVAILABLE == 4503


def test_stream_rejects_missing_hub_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        app.state.stream = None
        headers = dict(WS_HEADERS)
        cookie = client.cookies.get(COOKIE_NAME)
        headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
        _must_reject(client, headers, code=CLOSE_UNAVAILABLE)


def test_wait_primed_times_out_and_still_emits_hello() -> None:
    hub = EventHub(
        tracker=RevisionTracker(),
        cache=TimelineCache(),
        member_status=MemberStatusService(()),
        health=None,
        tmux=None,
        settings=StreamSettings(
            hello_wait_s=0.05,
            ping_interval_s=3600.0,
            idle_timeout_s=3600.0,
            member_interval_s=3600.0,
            health_interval_s=3600.0,
        ),
    )
    asyncio.run(hub.wait_primed())
    hello = hub.hello()
    assert hello["type"] == "hello"
    assert hello["epoch"] == hub.tracker.epoch


def test_hello_subscribe_and_timeline_work_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace, service, paths = _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["epoch"] == client.get("/api/v1/session").json()["epoch"]
            assert hello["limits"]["queue"] == 256
            assert hello["limits"]["rings"] == {"timeline": 512, "work": 512, "health": 128}
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["timeline", "work"],
                    "known": hello["revisions"],
                }
            )
            deposit(
                Message.create("sonnet", "第二封", sender="fable", task="T-001"),
                paths,
            )
            app.state.stream.scan_files_now()
            timeline = ws.receive_json()
            assert timeline["domain"] == "timeline"
            assert timeline["type"] == "delta"
            assert any(op["op"] == "append" for op in timeline["ops"])
            listed = client.get("/api/v1/timeline").json()
            assert any(entry["text"] == "第二封" for entry in listed["entries"])
            assert listed["head_seq"] >= 2

            service.progress("sonnet", "T-001", "已定位回归")
            app.state.stream.scan_files_now()
            frames = [ws.receive_json()]
            if frames[0]["domain"] != "work":
                frames.append(ws.receive_json())
            work = next(item for item in frames if item["domain"] == "work")
            assert work["type"] == "delta"
            assert any(op["op"] == "append" and op["task_id"] == "T-001" for op in work["ops"])
            assert {"op": "invalidate", "scope": "tasks"} in work["ops"]

            snapshot = client.get("/api/v1/work").json()
            assert snapshot["tasks"][0]["status"] == "in-progress"


def test_subscribe_epoch_mismatch_and_invalidation_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = WebSession.generate()
    _bound_workspace(tmp_path, monkeypatch)
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client) as ws:
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": "deadbeefdeadbeef",
                    "domains": ["member"],
                    "known": {"member": 0},
                }
            )
            changed = ws.receive_json()
            assert changed["type"] == "epoch_changed"
            assert changed["epoch"] == hello["epoch"]
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["member", "timeline"],
                    "known": {"member": -1, "timeline": -1},
                }
            )
            first = ws.receive_json()
            second = ws.receive_json()
            kinds = {first["domain"]: first, second["domain"]: second}
            assert kinds["member"]["type"] == "resync"
            assert kinds["member"]["reason"] == "gap"
            assert kinds["timeline"]["type"] == "resync"
            assert kinds["timeline"]["reason"] == "gap"


def test_member_invalidation_after_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client) as ws:
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["member"],
                    "known": hello["revisions"],
                }
            )
            app.state.member_status.record_output("claude", b"working")
            app.state.stream.scan_members_now()
            member = ws.receive_json()
            assert member["type"] == "invalidation"
            assert member["domain"] == "member"
            cards = client.get("/api/v1/members").json()
            claude = next(item for item in cards["members"] if item["name"] == "claude")
            assert claude["state"] == "working"


def test_health_delta_raise_and_clear(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    health = HealthMonitor(paths, (), None, interval=10)
    hub = EventHub(
        tracker=RevisionTracker(),
        cache=TimelineCache(),
        member_status=MemberStatusService(()),
        health=health,
        tmux=None,
        settings=FAST,
    )
    client = hub.add_client()
    client.domains.add("health")
    fault = Fault(FaultKind.BUS_UNWRITABLE, str(paths.queue), "unwritable")
    hub.apply_health_events(health.update({fault.key: fault}))
    assert list(client._pending) == []
    hub.apply_health_events(health.update({}))
    frame = client._pending.popleft()
    assert frame["type"] == "delta"
    assert frame["domain"] == "health"
    assert frame["ops"] == [
        {
            "op": "clear",
            "fault": {
                "key": fault.key,
                "kind": "bus-unwritable",
                "target": fault.target,
                "detail": fault.detail,
            },
        }
    ]


def test_two_clients_see_one_timeline_event_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace, _service, _paths = _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client) as left, _ws(client) as right:
            for sock in (left, right):
                hello = sock.receive_json()
                sock.send_json(
                    {
                        "type": "subscribe",
                        "epoch": hello["epoch"],
                        "domains": ["timeline"],
                        "known": hello["revisions"],
                    }
                )
            _freeze_work_clock(monkeypatch, datetime(2099, 1, 1, tzinfo=UTC))
            _service.progress("sonnet", "T-001", "双端")
            app.state.stream.scan_files_now()
            for sock in (left, right):
                frame = sock.receive_json()
                assert frame["type"] == "delta"
                assert frame["domain"] == "timeline"
                assert sum(1 for op in frame["ops"] if op["op"] == "append") == 1


def test_replay_timeline_after_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace, service, _paths = _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        with _ws(client) as ws:
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["timeline"],
                    "known": hello["revisions"],
                }
            )
            _freeze_work_clock(monkeypatch, datetime(2099, 1, 1, tzinfo=UTC))
            service.progress("sonnet", "T-001", "追平")
            app.state.stream.scan_files_now()
            first = ws.receive_json()
            assert first["type"] == "delta"
            known = first["revision"]
        with _ws(client) as ws:
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["timeline"],
                    "known": {**hello["revisions"], "timeline": known},
                }
            )
            _freeze_work_clock(monkeypatch, datetime(2099, 1, 2, tzinfo=UTC))
            service.progress("sonnet", "T-001", "再一条")
            app.state.stream.scan_files_now()
            replayed = ws.receive_json()
            assert replayed["type"] == "delta"
            assert replayed["domain"] == "timeline"
            assert replayed["revision"] == known + 1


def test_track_starts_watch_for_members_added_after_run() -> None:
    watched: list[str] = []

    class HangStream:
        def __init__(self, _tmux: object, pane_id: str) -> None:
            watched.append(pane_id)

        def __aiter__(self) -> HangStream:
            return self

        async def __anext__(self) -> str:
            await asyncio.Event().wait()
            raise StopAsyncIteration

        async def close(self) -> None:
            return None

    class FakeTmux:
        def list_panes(self, name: str, *, all_sessions: bool = False) -> list[PaneInfo]:
            return [PaneInfo(name, 0, 0, name, 1, "cat")]

    async def scenario() -> None:
        service = MemberStatusService(
            ("alpha",),
            FakeTmux(),
            reconnect_interval=0.05,
            stream_factory=HangStream,
        )
        task = asyncio.create_task(service.run())
        try:
            await asyncio.sleep(0.08)
            assert "alpha" in watched
            service.track(("alpha", "beta"))
            await asyncio.sleep(0.08)
            assert "beta" in watched
        finally:
            service.stop()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_timeline_middle_work_event_appends_without_renumbering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务事件 at 插在末条之前：旧 key 的 seq 不变，新记录 append。"""
    _workspace, service, _paths = _bound_workspace(tmp_path, monkeypatch)
    session = WebSession.generate()
    app = create_app(session=session, port=PORT, stream_settings=FAST)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.get(f"/?token={session.token}")
        app.state.stream.scan_files_now()
        before = client.get("/api/v1/timeline").json()
        seq_by_key = {entry["key"]: entry["seq"] for entry in before["entries"]}
        last_at_key = max(before["entries"], key=lambda item: (item["at"], item["seq"]))["key"]
        work_before = sum(1 for entry in before["entries"] if entry["key"].startswith("work:"))
        revision = before["revision"]
        _freeze_work_clock(monkeypatch, datetime(2020, 1, 1, tzinfo=UTC))
        with _ws(client) as ws:
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "subscribe",
                    "epoch": hello["epoch"],
                    "domains": ["timeline"],
                    "known": hello["revisions"],
                }
            )
            service.progress("sonnet", "T-001", "插在中间")
            app.state.stream.scan_files_now()
            frame = ws.receive_json()
            assert frame["type"] == "delta"
            assert frame["domain"] == "timeline"
            assert frame["revision"] == revision + 1
            assert any(op["op"] == "append" for op in frame["ops"])
        after = client.get("/api/v1/timeline").json()
        assert after["revision"] == revision + 1
        after_by_key = {entry["key"]: entry["seq"] for entry in after["entries"]}
        for key, seq in seq_by_key.items():
            assert after_by_key[key] == seq
        assert after["head_seq"] == before["head_seq"] + 1
        latest_at = max(after["entries"], key=lambda item: (item["at"], item["seq"]))
        assert latest_at["key"] == last_at_key
        work_after = sum(1 for entry in after["entries"] if entry["key"].startswith("work:"))
        assert work_after == work_before + 1
