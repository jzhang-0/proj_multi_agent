"""自建 adapter:本机跑一个群聊网页,手机浏览器打开就能用。

只用标准库(`http.server`),不引第三方 SDK、不需要任何平台账号——这是
选"自建"的理由。传输用**长轮询**而不是 WebSocket:

- 手机切后台、锁屏、换网络时连接必然会断,长轮询天然按"游标续传":
  客户端记住 `cursor`,重连时带上,断线期间的消息一条不丢(**断线重连**
  这条要求落在这里,而不是靠保活心跳);
- 标准库不带 WebSocket,自己实现握手与分帧不划算。

凭证见 `gateway.config`:页面和接口都要带 token。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gateway.base import GroupMessage, GroupPost
from gateway.config import GatewayConfig
from gateway.page import PAGE_HTML

#: 长轮询最多挂多久(秒)。比手机浏览器的空闲超时短
POLL_TIMEOUT = 25.0

#: 轮询检查间隔(秒)
POLL_STEP = 0.1

#: 内存里保留多少条历史,供断线重连补齐
HISTORY_LIMIT = 500


@dataclass
class Broadcast:
    """一条已经发进群的消息(带序号,断线重连按序号续传)。"""

    seq: int
    author: str
    text: str
    kind: str
    ts: str

    def as_dict(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "author": self.author,
            "text": self.text,
            "kind": self.kind,
            "ts": self.ts,
        }


class LocalChatAdapter:
    """自建群聊 adapter:HTTP 服务 + 长轮询。"""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.on_message: Callable[[GroupMessage], None] | None = None
        self._history: list[Broadcast] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- GatewayAdapter 接口 ---------------------------------------------

    async def start(self, on_message: Callable[[GroupMessage], None]) -> None:
        self.on_message = on_message
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    async def post(self, post: GroupPost) -> None:
        self.broadcast(post)

    # --- 群消息缓冲 -------------------------------------------------------

    def broadcast(self, post: GroupPost) -> Broadcast:
        with self._lock:
            self._seq += 1
            item = Broadcast(
                self._seq,
                post.author,
                post.text,
                post.kind,
                time.strftime("%H:%M:%S"),
            )
            self._history.append(item)
            del self._history[:-HISTORY_LIMIT]
            return item

    def since(self, cursor: int) -> list[Broadcast]:
        with self._lock:
            return [item for item in self._history if item.seq > cursor]

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def wait_for(self, cursor: int, timeout: float = POLL_TIMEOUT) -> list[Broadcast]:
        """长轮询:等到有新消息或超时。超时回空列表,客户端原样重来。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fresh = self.since(cursor)
            if fresh:
                return fresh
            time.sleep(POLL_STEP)
        return []

    # --- 收到群消息 -------------------------------------------------------

    def submit(self, user: str, text: str, *, room: str | None = None) -> None:
        if self.on_message is not None:
            self.on_message(GroupMessage(user=user, text=text, room=room or self.config.room))

    @property
    def port(self) -> int:
        """实际监听的端口(配置成 0 时由系统分配)。"""
        return self._server.server_address[1] if self._server else self.config.port


def lan_address() -> str:
    """本机在局域网里的地址,拼给手机用的 URL。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))  # 不发包,只让内核选一个出口地址
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _make_handler(adapter: LocalChatAdapter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:  # 别把访问日志刷进终端
            return

        # --- 工具 ---------------------------------------------------------

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _authorized(self, token: str | None) -> bool:
            return not adapter.config.token or token == adapter.config.token

        # --- 路由 ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (http.server 的接口)
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            token = (query.get("token") or [None])[0]

            if parsed.path in ("/", "/index.html"):
                self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/messages":
                if not self._authorized(token):
                    self._json(401, {"error": "口令不对"})
                    return
                cursor = int((query.get("since") or ["0"])[0])
                fresh = adapter.wait_for(cursor)
                self._json(
                    200,
                    {
                        "messages": [item.as_dict() for item in fresh],
                        "cursor": fresh[-1].seq if fresh else cursor,
                    },
                )
                return
            self._json(404, {"error": "没有这个接口"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/send":
                self._json(404, {"error": "没有这个接口"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "请求不是合法 JSON"})
                return
            if not self._authorized(str(payload.get("token", ""))):
                self._json(401, {"error": "口令不对"})
                return
            user = str(payload.get("user", "")).strip() or "anonymous"
            text = str(payload.get("text", "")).strip()
            if not text:
                self._json(400, {"error": "消息是空的"})
                return
            room = str(payload.get("workspace") or payload.get("room") or adapter.config.room)
            adapter.submit(user, text, room=room.strip() or adapter.config.room)
            self._json(200, {"ok": True})

    return Handler
