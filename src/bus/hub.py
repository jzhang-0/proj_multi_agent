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

#: `human` 是保留名:不投递,只上屏(架构决策 §3)
HUMAN = "human"


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


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


def tmux_deliver(message: Message) -> bool:
    """v0 行为:把消息"打字"进同名 tmux 会话并回车。"""
    if not tmux_session_exists(message.to):
        return False
    text = message.text.replace("\n", " ")
    line = (
        f"[群消息] 来自 {message.sender}: {text}"
        f' —— 如需回复,运行: ./msg {message.sender} "你的回复"'
    )
    subprocess.run(["tmux", "send-keys", "-t", message.to, "-l", line], check=True)
    time.sleep(0.3)  # 给输入框一点时间接住文本,再敲回车
    subprocess.run(["tmux", "send-keys", "-t", message.to, "Enter"], check=True)
    return True


class Hub:
    """扫描队列并投递的循环。"""

    def __init__(
        self,
        paths: BusPaths,
        deliver: Callable[[Message], bool] = tmux_deliver,
        on_result: Callable[[DeliveryResult], None] | None = None,
        poll_interval: float = 0.5,
        policy: OutboundPolicy | None = None,
    ) -> None:
        self.paths = paths.ensure()
        self.deliver = deliver
        self.on_result = on_result
        self.poll_interval = poll_interval
        self.policy = policy if policy is not None else OutboundPolicy()

    def _reject(self, path: Path, message: Message, reason: str) -> DeliveryResult:
        """拒收:消息不投递直接归档,给发件人回执一条说明。"""
        archive(path, self.paths)
        receipt = receipt_for(message, reason)
        if receipt is not None:
            deposit(receipt, self.paths)
        return DeliveryResult(path, DeliveryOutcome.REJECTED, message, reason)

    def _handle(self, path: Path) -> DeliveryResult:
        try:
            message = read_message(path)
        except MalformedMessage as exc:
            quarantine(path, self.paths, str(exc))
            return DeliveryResult(path, DeliveryOutcome.MALFORMED, detail=str(exc))

        if message.to == HUMAN:
            archive(path, self.paths)
            return DeliveryResult(path, DeliveryOutcome.SHOWN, message)

        verdict = self.policy.check(message)
        if not verdict.ok:
            return self._reject(path, message, verdict.reason)
        self.policy.record(message)

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
        for path in pending(self.paths):
            result = self._handle(path)
            results.append(result)
            if self.on_result is not None:
                self.on_result(result)
        return results

    def run(self, stop: Callable[[], bool] | None = None) -> None:
        """轮询直到 `stop()` 为真(默认永不停,靠 KeyboardInterrupt 退出)。"""
        while stop is None or not stop():
            self.drain_once()
            time.sleep(self.poll_interval)
