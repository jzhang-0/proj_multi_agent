"""投递循环。

循环的硬要求:**一条坏消息不能炸掉整个循环**。读不出来或结构非法的
文件进死信目录,投递函数自己抛异常也只记一次失败,循环继续跑下一条。

投递动作通过 `deliver` 注入,默认是 v0 的 tmux send-keys 行为;真正的
按键注入能力在 TMX 卷落地后由调用方替换进来。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bus.message import MalformedMessage, Message
from bus.paths import BusPaths
from bus.policy import OutboundPolicy, receipt_for
from bus.queue import archive, deposit, pending, quarantine, read_message
from bus.sanitize import format_for_injection

#: `human` 是保留名:不投递,只上屏(架构决策 §3)
HUMAN = "human"

#: watchfiles 内部的轮询步长(毫秒),压到最小换低延迟
WATCH_STEP_MS = 5

#: 同一批文件事件的合并窗口(毫秒)
WATCH_DEBOUNCE_MS = 20

#: watchfiles 不可用时的轮询间隔(秒)
FALLBACK_POLL_SECONDS = 0.2


def watchfiles_available() -> bool:
    """`watchfiles` 能不能用;不能就回退轮询,不让投递停摆。"""
    try:
        import watchfiles  # noqa: F401
    except Exception:
        return False
    return True


class DeliveryOutcome(StrEnum):
    """一条消息在这一轮里的处理结果。"""

    DELIVERED = "delivered"
    SHOWN = "shown"  # 发给 human,只上屏不投递
    FAILED = "deliver-failed"
    REJECTED = "rejected"  # 被防环策略挡下,发件人收到回执
    MALFORMED = "malformed"


@dataclass(frozen=True)
class DeliveryResult:
    path: Path
    outcome: DeliveryOutcome
    message: Message | None = None
    detail: str = ""


#: 敲回车前最多等目标终端回显多久(秒);等到就立刻回车,不再盲等
ECHO_TIMEOUT_SECONDS = 0.15

#: 等回显时的重试间隔(秒)
ECHO_POLL_SECONDS = 0.005


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


def format_line(message: Message) -> str:
    """成员终端里看到的那一行:清洗过的单行文本(见 `bus.sanitize`)。"""
    return format_for_injection(message)


def _capture(target: str) -> str | None:
    """取窗格当前画面;取不到返回 None(会话可能刚没了)。"""
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", target], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def _wait_for_echo(target: str, before: str | None, timeout: float = ECHO_TIMEOUT_SECONDS) -> None:
    """等目标窗格画面相对 `before` 发生变化,最多等 `timeout` 秒。

    v0 是盲等 0.3 秒,这在硬指标(入队 → 注入完成 P95 < 200ms)里直接超支。
    改成"看到终端有反应就回车":`cat` 窗格和多数 CLI 只要几毫秒。等不到也
    照样回车——宁可偶尔拼一次,也不能无限期卡住投递循环(半行拼接的根治在
    TMX-002)。
    """
    if before is None:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _capture(target) != before:
            return
        time.sleep(ECHO_POLL_SECONDS)


def tmux_deliver(message: Message) -> bool:
    """把消息"打字"进同名 tmux 会话并回车。"""
    if not tmux_session_exists(message.to):
        return False
    line = format_line(message)
    before = _capture(message.to)
    subprocess.run(["tmux", "send-keys", "-t", message.to, "-l", line], check=True)
    _wait_for_echo(message.to, before)
    subprocess.run(["tmux", "send-keys", "-t", message.to, "Enter"], check=True)
    return True


class Hub:
    """扫描队列并投递的循环。"""

    def __init__(
        self,
        paths: BusPaths,
        deliver: Callable[[Message], bool] = tmux_deliver,
        on_result: Callable[[DeliveryResult], None] | None = None,
        poll_interval: float = FALLBACK_POLL_SECONDS,
        policy: OutboundPolicy | None = None,
    ) -> None:
        self.paths = paths.ensure()
        self.deliver = deliver
        self.on_result = on_result
        self.poll_interval = poll_interval
        self.policy = policy if policy is not None else OutboundPolicy()
        #: 实际跑起来用的是 watch 还是 poll,起循环后才有值
        self.mode: str | None = None

    def _reject(self, path: Path, message: Message, reason: str) -> DeliveryResult:
        """拒收:消息不投递直接归档,给发件人回执一条说明。"""
        archive(path, self.paths)
        receipt = receipt_for(message, reason)
        if receipt is not None:
            deposit(receipt, self.paths)
        return DeliveryResult(path, DeliveryOutcome.REJECTED, message, reason)

    def _handle(
        self,
        path: Path,
        queued_before: dict[str, int] | None = None,
    ) -> DeliveryResult:
        try:
            message = read_message(path)
        except MalformedMessage as exc:
            quarantine(path, self.paths, str(exc))
            return DeliveryResult(path, DeliveryOutcome.MALFORMED, detail=str(exc))

        unread_backlog = 0
        if queued_before is not None:
            unread_backlog = queued_before.get(message.to, 0)
            queued_before[message.to] = unread_backlog + 1

        verdict = self.policy.check(message, unread_backlog=unread_backlog)
        if not verdict.ok:
            return self._reject(path, message, verdict.reason)
        self.policy.record(message)

        if message.to == HUMAN:
            archive(path, self.paths)
            return DeliveryResult(path, DeliveryOutcome.SHOWN, message)

        try:
            ok = self.deliver(message)
            detail = "" if ok else "没有这个 tmux 会话"
        except Exception as exc:  # 投递失败不能中断循环
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        archive(path, self.paths)
        outcome = DeliveryOutcome.DELIVERED if ok else DeliveryOutcome.FAILED
        return DeliveryResult(path, outcome, message, detail)

    def drain_once(self) -> list[DeliveryResult]:
        """处理当前队列里的全部消息,返回逐条结果。"""
        results = []
        queued_before: dict[str, int] = {}
        for path in pending(self.paths):
            result = self._handle(path, queued_before)
            results.append(result)
            if self.on_result is not None:
                self.on_result(result)
        return results

    def run(self, stop: Callable[[], bool] | None = None, watch: bool = True) -> None:
        """跑投递循环直到 `stop()` 为真(默认永不停,靠 KeyboardInterrupt 退出)。

        默认用 `watchfiles` 监听队列目录,新消息落盘立刻醒;`watchfiles` 装不上
        或起不来就自动退回轮询。两种模式都先清一次存量队列。
        """
        self.drain_once()
        if watch and watchfiles_available():
            self._run_watching(stop)
        else:
            self._run_polling(stop)

    def _run_polling(self, stop: Callable[[], bool] | None) -> None:
        self.mode = "poll"
        while stop is None or not stop():
            time.sleep(self.poll_interval)
            self.drain_once()

    def _run_watching(self, stop: Callable[[], bool] | None) -> None:
        import watchfiles

        self.mode = "watch"
        # yield_on_timeout + rust_timeout:没有文件事件时也定期醒一次,
        # 既能查 stop(),也能兜住极端情况下漏掉的事件。
        for _ in watchfiles.watch(
            self.paths.queue,
            step=WATCH_STEP_MS,
            debounce=WATCH_DEBOUNCE_MS,
            rust_timeout=int(self.poll_interval * 1000),
            yield_on_timeout=True,
            raise_interrupt=False,
        ):
            if stop is not None and stop():
                return
            self.drain_once()
