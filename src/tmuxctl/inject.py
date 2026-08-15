"""按键注入 API:字面文本 + Enter/Escape/C-c,长文本前做末行忙碌检测。"""

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

DEFAULT_MAX_WAIT_S = 0.6
DEFAULT_POLL_S = 0.1


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


@dataclass(frozen=True)
class InjectOutcome:
    """一次文本注入的隔离结果。"""

    waited: bool
    isolated: bool


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
