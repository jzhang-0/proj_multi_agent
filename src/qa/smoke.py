"""端到端冒烟:`uv run python -m qa.smoke`。

流程:起假成员 shell 窗格 → 消息入队 → hub 投递(TMX-002 KeyInjector) →
窗格画面出现令牌 → 清理。用临时 bus 根目录和隔离 tmux socket,不碰仓库根
`bus/` 与真实成员会话。成功时打印入队到窗格可见的延迟。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bus.hub import Hub, format_line
from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit
from tmuxctl import KeyInjector, Tmux

RECEIVE_DEADLINE_S = 3.0
HUB_READY_DEADLINE_S = 2.0
PANE_POLL_S = 0.005


@dataclass(frozen=True)
class SmokeReport:
    """一次冒烟的结果。"""

    ok: bool
    latency_ms: float | None
    detail: str
    token: str = ""


def _wait_until(predicate: Callable[[], bool], deadline_s: float) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(PANE_POLL_S)
    return False


def run_smoke() -> SmokeReport:
    """跑一整轮冒烟。任何路径都要清掉临时会话和临时目录。"""
    token = uuid.uuid4().hex[:8]
    session = f"smoke{token}"
    socket = f"qa002-{token}"
    root = Path(tempfile.mkdtemp(prefix="qa-smoke-"))
    tmux: Tmux | None = None
    stop = threading.Event()
    worker: threading.Thread | None = None
    try:
        tmux = Tmux(socket_name=socket, timeout=5.0)
        tmux.new_session(
            session,
            command=["bash", "--norc", "--noprofile"],
            env={"PS1": "$ ", "TERM": "dumb"},
        )
        if not tmux.has_session(session):
            return SmokeReport(False, None, "假成员窗格没有拉起来", token)

        injector = KeyInjector(tmux)
        lines: dict[str, str] = {}

        def deliver(message: Message) -> bool:
            if not tmux.has_session(message.to):
                return False
            line = format_line(message)
            lines[message.to] = line
            injector.deliver(message.to, line)
            return True

        def confirm(target: str) -> bool:
            line = lines.pop(target, None)
            return True if line is None else injector.ensure_submitted(target, line).submitted

        paths = BusPaths.resolve(root).ensure()
        hub = Hub(paths, deliver=deliver, confirm=confirm)
        worker = threading.Thread(target=hub.run, kwargs={"stop": stop.is_set}, daemon=True)
        worker.start()
        if not _wait_until(lambda: hub.mode is not None, HUB_READY_DEADLINE_S):
            return SmokeReport(False, None, "投递循环没有进入 watch/poll", token)

        message = Message.create(to=session, text=f"smoke-{token}", sender="human")
        t0 = time.perf_counter_ns()
        deposit(message, paths)

        def pane_has_token() -> bool:
            return token in tmux.capture_pane(session)

        if not _wait_until(pane_has_token, RECEIVE_DEADLINE_S):
            snapshot = tmux.capture_pane(session)
            return SmokeReport(
                False,
                None,
                f"窗格未在 {RECEIVE_DEADLINE_S:.0f}s 内收到令牌 {token!r};画面={snapshot!r}",
                token,
            )
        latency_ms = (time.perf_counter_ns() - t0) / 1e6
        return SmokeReport(True, latency_ms, hub.mode or "", token)
    except Exception as exc:
        return SmokeReport(False, None, f"{type(exc).__name__}: {exc}", token)
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=2.0)
        if tmux is not None:
            tmux.kill_server()
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。argv 保留给以后加 flag,当前忽略。"""
    del argv
    report = run_smoke()
    if report.ok and report.latency_ms is not None:
        print(
            f"qa.smoke: ok  enqueue→pane {report.latency_ms:.1f}ms  "
            f"mode={report.detail}  token={report.token}"
        )
        return 0
    print(f"qa.smoke: FAIL  {report.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
