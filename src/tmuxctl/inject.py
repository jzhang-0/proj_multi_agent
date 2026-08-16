"""按键注入 API:字面文本 + Enter/Escape/C-c,长文本前做末行忙碌检测。

注入分两种用法:

- `text()`:通用注入,注入前先看末行有没有没提交的半行字(TMX-002)。
- `deliver()` + `ensure_submitted()`:投递专用。前者一次 tmux 调用注入并回车,
  后者事后确认「字真的提交出去了」,没提交就补 Enter。GATE-003 实测暴露过
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
#: 确认没提交时最多补几次 Enter
DEFAULT_SUBMIT_RETRIES = 2
#: 用文本尾部这么多个字符做「还压在输入框里」的指纹(尾部才是光标所在处)
FINGERPRINT_CHARS = 16


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
    """`deliver()` / `ensure_submitted()` 额外需要的两个批量命令。"""

    def send_line(self, target: str, text: str) -> None: ...

    def capture_with_cursor(self, target: str) -> tuple[str, int]: ...


@dataclass(frozen=True)
class InjectOutcome:
    """一次文本注入的隔离结果。"""

    waited: bool
    isolated: bool


@dataclass(frozen=True)
class SubmitOutcome:
    """一次提交确认的结果。

    `submitted` 为 False 表示补完 Enter 仍然看见那行字压在光标处——这时候
    消息很可能真的没进去,调用方该把它记成投递失败。
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
    """取文本尾部做指纹:去掉空白和边框,只留最后一小段。

    尾部而不是开头:长消息在输入框里会折行,光标停在最后一折上。
    """
    squashed = _DECORATION.sub("", text)
    return squashed[-FINGERPRINT_CHARS:]


def cursor_line_holds(snapshot: str, cursor_y: int, text: str) -> bool:
    """光标那行(或它上面一行,如果光标行是空的)还压着这段文本吗?

    提交成功后输入框会清空,光标就不再停在这段字上;而消息被 CLI 收下后
    回显到对话区的那一份不在光标行上,所以不会误判成「没提交」。
    """
    mark = fingerprint(text)
    if not mark:
        return False
    lines = snapshot.splitlines()
    if not 0 <= cursor_y < len(lines):
        return False
    if mark in _DECORATION.sub("", lines[cursor_y]):
        return True
    # Enter 被当成输入框内换行时,字留在紧邻的上一行、光标停在新的空行上。
    # 只看紧邻的那一行:跨过空行往上找会把对话区里已提交的回显认成没提交。
    if lines[cursor_y].strip() or cursor_y == 0:
        return False
    return mark in _DECORATION.sub("", lines[cursor_y - 1])


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

    def deliver(self, target: str, text: str) -> None:
        """投递专用:一次 tmux 调用注入文本并回车,不做前置画面检测。

        前置检测对成员 CLI 没有判别力(它们底部都常驻状态栏,末行永远"不像
        提示符"),却要多付一次 capture-pane 和一次隔离用的 Enter——那个 Enter
        本身还会在成员正忙时乱按一下。改成事后 `ensure_submitted()` 确认。
        """
        self._tmux.send_line(target, text)

    def ensure_submitted(
        self,
        target: str,
        text: str,
        *,
        retries: int = DEFAULT_SUBMIT_RETRIES,
        delay_s: float = DEFAULT_CONFIRM_DELAY_S,
    ) -> SubmitOutcome:
        """确认这段文本已经提交出去,没提交就补 Enter(最多 `retries` 次)。

        只在**看得见证据**时才补 Enter:光标停在自己刚注入的那行字上。看不出
        来就当它已经提交(实测 cursor 这类 CLI 把终端光标常驻在画面底部,压根
        不停在输入框那行,判不了)——宁可漏补,也不能对着别人的确认弹窗乱敲
        Enter。这类窗格靠的是 `deliver()` 把文本和 Enter 塞进同一次 tmux 调用:
        中间没有让 CLI 重绘的缝隙,实测正忙的 cursor 也能一次提交成功。
        """
        for attempt in range(retries + 1):
            if delay_s > 0:
                self._sleep(delay_s)
            snapshot, cursor_y = self._tmux.capture_with_cursor(target)
            if not cursor_line_holds(snapshot, cursor_y, text):
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
