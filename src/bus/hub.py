"""投递循环。

循环的硬要求:**一条坏消息不能炸掉整个循环**。读不出来或结构非法的
文件进死信目录,投递函数自己抛异常也只记一次失败,循环继续跑下一条。

投递动作通过 `deliver` 注入,默认是 v0 的 tmux send-keys 行为;真正的
按键注入能力在 TMX 卷落地后由调用方替换进来。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bus.audit import AuditEvent, AuditLog
from bus.message import REMOTE_PREFIX, MalformedMessage, Message
from bus.paths import BusPaths
from bus.policy import OutboundPolicy, receipt_for
from bus.queue import archive, deposit, pending, quarantine, read_message
from bus.sanitize import format_for_injection

#: `human` 是保留名:不投递,只上屏(架构决策 §3)
HUMAN = "human"


def is_screen_only(name: str) -> bool:
    """这个收件人该不该被注入终端。

    `human` 是人,`im:*` 是 IM 网关代投的远程身份——两者都没有 tmux 会话,
    投递循环只把它们上屏,由总控台/网关各自呈现。
    """
    return name == HUMAN or name.startswith(REMOTE_PREFIX)

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


#: 处理结果 → 审计事件。发给 human 的消息算送达(送到人的屏幕上)
_OUTCOME_EVENTS = {
    DeliveryOutcome.DELIVERED: AuditEvent.DELIVER,
    DeliveryOutcome.SHOWN: AuditEvent.DELIVER,
    DeliveryOutcome.FAILED: AuditEvent.DELIVER_FAILED,
    DeliveryOutcome.REJECTED: AuditEvent.REJECTED,
}


#: tmux 客户端只建一次:每次构造都会去探一遍版本,投递路径上不该重复付这个钱
_TMUX_CLIENT: object | None = None

#: 已注入、还没确认提交的那行字(收件人 → 文本)。同一收件人只留最后一条
_PENDING_SUBMITS: dict[str, str] = {}


def _tmux_client():
    """懒加载的 tmux 客户端(tmuxctl 是拼 tmux 命令的唯一出口,架构 §1)。"""
    global _TMUX_CLIENT
    if _TMUX_CLIENT is None:
        from workspace.session import bind_tmux

        _TMUX_CLIENT = bind_tmux()
    return _TMUX_CLIENT


def format_line(message: Message) -> str:
    """成员终端里看到的那一行:清洗过的单行文本(见 `bus.sanitize`)。"""
    return format_for_injection(message)


def tmux_deliver(message: Message) -> bool:
    """把消息"打字"进同名 tmux 会话并回车。

    注入交给 TMX-002 的 `KeyInjector.deliver()`:一次 tmux 调用发完文本和
    Enter。投递路径上每多一次 tmux 进程启动就多几十毫秒(P95 < 200ms 的预算
    经不起),所以注入前不再抓画面,提交是否真的生效改由 `confirm_submitted()`
    在这一轮投递之后确认。
    """
    from tmuxctl import KeyInjector, TmuxCommandError, TmuxError

    line = format_line(message)
    try:
        tmux = _tmux_client()
        KeyInjector(tmux).deliver(message.to, line)
    except TmuxCommandError:
        return False  # 会话不在(或窗格没了):算投递失败,不是崩溃
    except TmuxError:
        return False
    _PENDING_SUBMITS[message.to] = line
    return True


def confirm_submitted(target: str) -> bool:
    """确认上一条注入 `target` 的消息真的提交出去了,没有就补 Enter。

    GATE-003 实测暴露的缺陷:成员 CLI 正在收尾上一轮时,文本进了输入框但那
    一下 Enter 没生效,消息就卡在输入框里等人手动回车。这一步不在投递延迟的
    预算里——它跑在一轮投递之后,不占"入队 → 注入终端"的时间。
    """
    line = _PENDING_SUBMITS.pop(target, None)
    if line is None:
        return True
    from tmuxctl import KeyInjector, TmuxError

    try:
        tmux = _tmux_client()
        return KeyInjector(tmux).ensure_submitted(target, line).submitted
    except TmuxError:
        return False


class Hub:
    """扫描队列并投递的循环。"""

    def __init__(
        self,
        paths: BusPaths,
        deliver: Callable[[Message], bool] = tmux_deliver,
        on_result: Callable[[DeliveryResult], None] | None = None,
        poll_interval: float = FALLBACK_POLL_SECONDS,
        policy: OutboundPolicy | None = None,
        audit: AuditLog | None = None,
        confirm: Callable[[str], bool] | None = confirm_submitted,
    ) -> None:
        self.paths = paths.ensure()
        self.audit = audit if audit is not None else AuditLog(self.paths)
        self.deliver = deliver
        self.confirm = confirm
        self.on_result = on_result
        self.poll_interval = poll_interval
        self.policy = policy if policy is not None else OutboundPolicy()
        #: 最近一轮的提交确认线程(见 `_confirm_submits`)
        self._confirm_thread: threading.Thread | None = None
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
            self.audit.record_malformed(path, str(exc))
            return DeliveryResult(path, DeliveryOutcome.MALFORMED, detail=str(exc))

        unread_backlog = 0
        if queued_before is not None:
            unread_backlog = queued_before.get(message.to, 0)
            queued_before[message.to] = unread_backlog + 1

        verdict = self.policy.check(message, unread_backlog=unread_backlog)
        if not verdict.ok:
            return self._reject(path, message, verdict.reason)
        self.policy.record(message)

        if is_screen_only(message.to):
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

    def _audit(self, result: DeliveryResult) -> None:
        """把处理结果记进审计日志;记日志失败不许影响投递。"""
        if result.message is None:
            return
        event = _OUTCOME_EVENTS.get(result.outcome)
        if event is None:
            return
        with contextlib.suppress(OSError):
            self.audit.record(event, result.message, result.detail)

    def _confirm_worker(self, pairs: list[tuple[str, Message]]) -> None:
        """逐个收件人确认「注入的那行字真的提交出去了」,确认不了记一笔审计。"""
        assert self.confirm is not None
        for target, message in pairs:
            try:
                ok = self.confirm(target)
            except Exception:  # 确认出岔子不能中断循环
                ok = False
            if ok:
                continue
            with contextlib.suppress(OSError):
                self.audit.record(AuditEvent.DELIVER_FAILED, message, "注入后补 Enter 仍卡在输入框")

    def _confirm_submits(self, results: list[DeliveryResult]) -> None:
        """把这一轮的提交确认交给后台线程。

        确认要给成员 CLI 一点处理时间(~0.1 秒)再抓画面,挂在投递循环上会把
        下一条消息的投递延迟一起抬高(实测 P95 194ms → 345ms)。指纹只认自己
        注入的那行字,所以后台线程即使和新的注入撞上,也不会替别人乱敲 Enter。
        确认结果不改这一轮已经报给调用方的投递结果。
        """
        if self.confirm is None:
            return
        latest: dict[str, Message] = {}
        for result in results:
            if result.outcome is not DeliveryOutcome.DELIVERED or result.message is None:
                continue
            latest[result.message.to] = result.message
        if not latest:
            return
        thread = threading.Thread(
            target=self._confirm_worker,
            args=(list(latest.items()),),
            name="bus-confirm",
            daemon=True,
        )
        self._confirm_thread = thread
        thread.start()

    def wait_for_confirms(self, timeout: float = 5.0) -> None:
        """等后台确认线程收工(测试和一次性收口用;循环本身不等它)。"""
        thread = self._confirm_thread
        if thread is not None:
            thread.join(timeout)

    def drain_once(self) -> list[DeliveryResult]:
        """处理当前队列里的全部消息,返回逐条结果。"""
        results = []
        queued_before: dict[str, int] = {}
        for path in pending(self.paths):
            result = self._handle(path, queued_before)
            results.append(result)
            self._audit(result)
            if self.on_result is not None:
                self.on_result(result)
        self._confirm_submits(results)
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
