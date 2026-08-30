"""按键注入 API:字面文本 + Enter/Escape/C-c,长文本前做末行忙碌检测。

注入分两种用法:

- `text()`:通用注入,注入前先看末行有没有没提交的半行字(TMX-002)。
- `deliver()` + `ensure_submitted()`:投递专用。前者把文本和回车分开发送,
  后者在归档前确认「字真的提交出去了」,没提交就补 Enter。GATE-003 实测暴露过
  这个缺口:成员 CLI 正忙时,文本进了输入框但那一下 Enter 没生效,消息就卡在
  输入框里等人手动回车,总线这边却已经记成投递成功。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

KeyName = Literal["Enter", "Escape", "C-c"]

# 末行以常见提示符收尾 → 视为输入框空闲(无未提交内容)。
_PROMPT_END = re.compile(r"(?:>>>|[>#%$❯➜])\s*$")
# TUI 边框/分隔线不算未提交输入。
_BOX_ONLY = re.compile(r"^[\s\-_|─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬╭╮╯╰▀▄]+$")
# 比对光标行时先抹掉的装饰:空白与边框字符。
_DECORATION = re.compile(r"[\s\-_|─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬╭╮╯╰▀▄]+")

DEFAULT_MAX_WAIT_S = 0.6
DEFAULT_POLL_S = 0.1

#: 确认提交前先给 CLI 的处理时间(秒)
DEFAULT_CONFIRM_DELAY_S = 0.12
#: 文本和 Enter 必须分开发送并留出粘贴突发判定时间。Claude/Codex 这类
#: TUI 收到同一批里的 Enter 时会把它吞进粘贴而不提交。
DEFAULT_SUBMIT_GAP_S = 0.01
#: 确认没提交时最多补几次 Enter
DEFAULT_SUBMIT_RETRIES = 2
# Claude 的 composer 上下边框。光标可能常驻最底部状态区，不能再以光标行
# 作为唯一证据；消息处在靠近底部的最后一对横线之间时，就是仍在输入框。
_HORIZONTAL_RULE = re.compile(r"^\s*[─━═-]{8,}\s*$")
_COMPOSER_TAIL_LINES = 6


class InjectTarget(Protocol):
    """KeyInjector 需要的最小 tmux 面。"""

    def capture_pane(
        self,
        target: str,
        *,
        escape: bool = False,
        start: int | str | None = None,
        end: int | str | None = None,
    ) -> str: ...

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None: ...


class DeliverTarget(InjectTarget, Protocol):
    """`deliver()` / `ensure_submitted()` 额外需要的画面探针。"""

    def capture_with_cursor(self, target: str) -> tuple[str, int]: ...


@dataclass(frozen=True)
class InjectOutcome:
    """一次文本注入的隔离结果。"""

    waited: bool
    isolated: bool


@dataclass(frozen=True)
class SubmitOutcome:
    """一次提交确认的结果。

    `submitted` 为 False 表示补完 Enter 后，composer 里仍然看见那行字——
    这时候消息还没进去，调用方必须保留队列等待重试。
    """

    submitted: bool
    retries: int


def last_line_uncommitted(snapshot: str) -> bool:
    """capture-pane 末行启发式:是否像有未提交的半行输入。

    tmux 会把画面补齐到窗格高度,末尾常有空行,所以从下往上跳过空行和边框,
    再判断第一条内容行。
    """
    candidate = ""
    for line in reversed(snapshot.splitlines()):
        if not line.strip() or _BOX_ONLY.fullmatch(line):
            continue
        candidate = line
        break
    if not candidate.strip():
        return False
    inner = candidate.strip().strip("│┃").strip()
    if not inner:
        return False
    return _PROMPT_END.search(inner) is None and _PROMPT_END.search(candidate) is None


def fingerprint(text: str) -> str:
    """生成待提交文本指纹：去掉折行空白和边框，保留完整正文。

    总线消息的回复提示尾部高度重复，截尾会把同一发件人的不同消息误认成
    同一条；完整正文压平后仍能覆盖终端自动折行，而且不会发生这种碰撞。
    """
    return _DECORATION.sub("", text)


def cursor_line_holds(snapshot: str, cursor_y: int, text: str) -> bool:
    """光标所在的连续输入块还压着这段文本吗?

    提交成功后输入框会清空,光标就不再停在这段字上;而消息被 CLI 收下后
    回显到对话区的那一份不在光标行上,所以不会误判成「没提交」。
    """
    mark = fingerprint(text)
    if not mark:
        return False
    lines = snapshot.splitlines()
    if not 0 <= cursor_y < len(lines):
        return False
    # shell 已经执行完并回到 `$ ` / `> ` 等提示符时，历史区紧挨着光标也
    # 不能算待提交输入；提示符后还有正文时则不会命中这个“行尾提示符”。
    if lines[cursor_y].strip() and _PROMPT_END.search(lines[cursor_y]):
        return False
    end = cursor_y
    # Enter 被当成输入框内换行时，字留在紧邻的上一行、光标停在新的空行上。
    # 上一行也是空行则说明输入框已经清空，不能跨过去匹配对话区回显。
    if not lines[end].strip():
        if end == 0 or not lines[end - 1].strip():
            return False
        end -= 1
    start = end
    while start > 0 and lines[start - 1].strip():
        start -= 1
    candidate = _DECORATION.sub("", "".join(lines[start : end + 1]))
    if not candidate:
        return False
    return mark in candidate


def framed_composer_holds(snapshot: str, text: str) -> bool | None:
    """识别 Claude 风格的底部双横线 composer。

    返回 ``None`` 表示画面没有这种结构，应回退其他探针；有结构时，消息
    指纹仍在最后一对横线之间就是未提交。把整块压平后比对，覆盖中文折行。
    """
    mark = fingerprint(text)
    if not mark:
        return False
    lines = snapshot.splitlines()
    rules = [index for index, line in enumerate(lines) if _HORIZONTAL_RULE.fullmatch(line)]
    if len(rules) < 2:
        return None
    lower = rules[-1]
    if len(lines) - lower - 1 > _COMPOSER_TAIL_LINES:
        return None
    upper = rules[-2]
    composer = _DECORATION.sub("", "".join(lines[upper + 1 : lower]))
    return mark in composer


def submission_still_pending(snapshot: str, cursor_y: int, text: str) -> bool:
    """消息是否仍在成员输入框，兼容新版 Claude 的底部驻留光标。"""
    framed = framed_composer_holds(snapshot, text)
    if framed is not None:
        return framed
    return cursor_line_holds(snapshot, cursor_y, text)


class KeyInjector:
    """向成员窗格注入文本或控制键。

    注入长文本前先 capture-pane 看末行:有未提交内容则等待;超时仍忙碌则先
    敲 Enter 换行隔离,避免拼到半行字上(v0 已知问题)。
    """

    def __init__(
        self,
        tmux: InjectTarget,
        *,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        poll_s: float = DEFAULT_POLL_S,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._tmux = tmux
        self._max_wait_s = max_wait_s
        self._poll_s = poll_s
        self._now = clock or time.monotonic
        self._sleep = sleeper or time.sleep

    def _busy(self, target: str) -> bool:
        return last_line_uncommitted(self._tmux.capture_pane(target))

    def _prepare(self, target: str) -> InjectOutcome:
        waited = False
        isolated = False
        deadline = self._now() + self._max_wait_s
        while self._busy(target):
            waited = True
            if self._now() >= deadline:
                self._tmux.send_keys(target, "Enter")
                isolated = True
                break
            remaining = max(0.0, deadline - self._now())
            self._sleep(min(self._poll_s, remaining))
        return InjectOutcome(waited=waited, isolated=isolated)

    def text(self, target: str, text: str, *, submit: bool = True) -> InjectOutcome:
        """以 `-l` 字面模式注入文本,默认再敲 Enter。"""
        outcome = self._prepare(target)
        self._tmux.send_keys(target, text, literal=True)
        if submit:
            self._tmux.send_keys(target, "Enter")
        return outcome

    def deliver(
        self,
        target: str,
        text: str,
        *,
        gap_s: float = DEFAULT_SUBMIT_GAP_S,
    ) -> None:
        """投递专用：文本与 Enter 分两次发送，中间等待 CLI 结束粘贴判定。"""
        self._tmux.send_keys(target, text, literal=True)
        if gap_s > 0:
            self._sleep(gap_s)
        self._tmux.send_keys(target, "Enter")

    def ensure_submitted(
        self,
        target: str,
        text: str,
        *,
        retries: int = DEFAULT_SUBMIT_RETRIES,
        delay_s: float = DEFAULT_CONFIRM_DELAY_S,
    ) -> SubmitOutcome:
        """确认这段文本已经提交出去,没提交就补 Enter(最多 `retries` 次)。

        Claude 的双横线 composer 直接检查整块输入区；其他 CLI 才回退光标行
        指纹。只有明确看见消息仍在输入框时才补 Enter，避免对确认弹窗盲按。
        """
        for attempt in range(retries + 1):
            if delay_s > 0:
                self._sleep(delay_s)
            snapshot, cursor_y = self._tmux.capture_with_cursor(target)
            if not submission_still_pending(snapshot, cursor_y, text):
                return SubmitOutcome(submitted=True, retries=attempt)
            if attempt == retries:
                break
            self._tmux.send_keys(target, "Enter")
        return SubmitOutcome(submitted=False, retries=retries)

    def key(self, target: str, name: KeyName) -> None:
        """注入 Enter / Escape / C-c。控制键不做忙碌检测。"""
        self._tmux.send_keys(target, name)

    def enter(self, target: str) -> None:
        self.key(target, "Enter")

    def escape(self, target: str) -> None:
        self.key(target, "Escape")

    def interrupt(self, target: str) -> None:
        self.key(target, "C-c")


def inject_text(
    tmux: InjectTarget, target: str, text: str, *, submit: bool = True
) -> InjectOutcome:
    """便捷入口:用默认等待参数注入字面文本。"""
    return KeyInjector(tmux).text(target, text, submit=submit)
