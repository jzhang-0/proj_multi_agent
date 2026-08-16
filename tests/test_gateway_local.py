"""GATE-002:自建 adapter 的接口、凭证与断线重连(按游标续传)。"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from gateway.base import GroupPost
from gateway.config import GatewayConfig
from gateway.local import LocalChatAdapter


@pytest.fixture
def adapter():
    """监听 127.0.0.1 的随机端口,测完关掉。"""
    config = GatewayConfig(host="127.0.0.1", port=0, token="口令123", room="default")
    adapter = LocalChatAdapter(config)
    received = []
    asyncio.run(adapter.start(received.append))
    adapter.received = received
    try:
        yield adapter
    finally:
        asyncio.run(adapter.stop())


def get(adapter, path):
    """token 里可能有中文,和真实客户端一样要转义。"""
    if "token=" in path:
        head, _, token = path.partition("token=")
        path = head + "token=" + urllib.parse.quote(token)
    url = f"http://127.0.0.1:{adapter.port}{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.status, response.read().decode("utf-8")


def post(adapter, payload):
    url = f"http://127.0.0.1:{adapter.port}/api/send"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


# --- 页面与凭证 ---------------------------------------------------------


def test_page_is_served_without_external_dependencies(adapter):
    status, body = get(adapter, "/")
    assert status == 200
    assert "AI 群聊" in body
    # 手机可能没有外网:页面不许引任何外部资源
    for marker in ("http://", "https://", "cdn", "<script src"):
        assert marker not in body.replace("http://127.0.0.1", ""), marker


def test_token_is_required_for_both_directions(adapter):
    code, payload = post(adapter, {"user": "小明", "text": "@claude 干活", "token": "错的"})
    assert code == 401 and "口令" in payload["error"]
    assert adapter.received == []

    with pytest.raises(urllib.error.HTTPError) as caught:
        get(adapter, "/api/messages?since=0&token=错的")
    assert caught.value.code == 401


def test_message_from_the_page_reaches_the_gateway(adapter):
    code, payload = post(adapter, {"user": "小明", "text": "@claude 干活", "token": "口令123"})
    assert (code, payload) == (200, {"ok": True})
    assert [(m.user, m.text, m.room) for m in adapter.received] == [
        ("小明", "@claude 干活", "default")
    ]


def test_empty_message_is_refused(adapter):
    code, payload = post(adapter, {"user": "小明", "text": "   ", "token": "口令123"})
    assert code == 400 and "空" in payload["error"]


# --- 断线重连:按游标续传 -----------------------------------------------


def test_client_resumes_from_its_cursor_after_a_drop(adapter):
    asyncio.run(adapter.post(GroupPost("claude", "第一条")))
    status, body = get(adapter, "/api/messages?since=0&token=口令123")
    first = json.loads(body)
    assert status == 200
    assert [m["text"] for m in first["messages"]] == ["第一条"]
    cursor = first["cursor"]

    # 模拟客户端断线:这期间群里又说了两句
    asyncio.run(adapter.post(GroupPost("codex", "断线期间第二条")))
    asyncio.run(adapter.post(GroupPost("bus", "断线期间第三条", kind="notice")))

    # 重连时带上老游标,断线期间的消息一条不丢
    _, body = get(adapter, f"/api/messages?since={cursor}&token=口令123")
    resumed = json.loads(body)
    assert [m["text"] for m in resumed["messages"]] == ["断线期间第二条", "断线期间第三条"]
    assert [m["kind"] for m in resumed["messages"]] == ["message", "notice"]
    assert resumed["cursor"] > cursor


def test_long_poll_returns_as_soon_as_a_message_arrives(adapter):
    async def scenario():
        loop = asyncio.get_running_loop()
        waiting = loop.run_in_executor(
            None, get, adapter, "/api/messages?since=0&token=口令123"
        )
        await asyncio.sleep(0.2)
        await adapter.post(GroupPost("claude", "轮询等到的消息"))
        status, body = await asyncio.wait_for(waiting, timeout=10)
        return status, json.loads(body)

    status, payload = asyncio.run(scenario())
    assert status == 200
    assert [m["text"] for m in payload["messages"]] == ["轮询等到的消息"]


def test_history_is_bounded(adapter):
    from gateway.local import HISTORY_LIMIT

    for index in range(HISTORY_LIMIT + 20):
        adapter.broadcast(GroupPost("claude", f"第 {index} 条"))
    assert len(adapter.since(0)) == HISTORY_LIMIT
    assert adapter.cursor == HISTORY_LIMIT + 20


# --- 凭证配置 -----------------------------------------------------------


def test_config_generates_and_persists_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    target = tmp_path / "gateway.toml"
    config = GatewayConfig().ensure_token(target)

    assert config.token
    assert target.is_file()
    assert config.token in target.read_text(encoding="utf-8")
    # 已经有 token 就不再改
    assert GatewayConfig.load(target).token == config.token
    assert GatewayConfig.load(target).ensure_token(target).token == config.token


def test_env_overrides_the_file(tmp_path, monkeypatch):
    target = tmp_path / "gateway.toml"
    target.write_text(GatewayConfig(port=1234, token="文件里的").as_toml(), encoding="utf-8")
    monkeypatch.setenv("GATEWAY_TOKEN", "环境变量的")
    monkeypatch.setenv("GATEWAY_PORT", "4321")

    config = GatewayConfig.load(target)
    assert (config.token, config.port) == ("环境变量的", 4321)


def test_url_carries_the_token():
    config = GatewayConfig(port=8765, token="abc")
    assert config.url_for("192.168.1.7") == "http://192.168.1.7:8765/?token=abc"
