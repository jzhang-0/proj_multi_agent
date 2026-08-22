"""输入框:人在总控台里说话的地方。

四件事:

- **回车即发**;
- **`@` 触发成员名补全**,Tab 或方向键选,Tab / 回车落定;
- **不写 `@` 就发给上一个对话对象**,占位符里写清楚这次会发给谁;
- **上下键翻自己发过的话**(补全没开的时候)。

按键的优先级是有讲究的:补全开着时 ↑↓ 是选候选,补全关着时 ↑↓ 才是翻
历史——同一组键在两种状态下做两件事,靠 `candidates` 是否为空区分。
"""

from __future__ import annotations

import re

from textual import events
from textual.message import Message
from textual.widgets import Input

from console.commands import COMMAND_NAMES

#: 光标前正在输入的 `@前缀`(允许中文名字)
AT_PREFIX = re.compile(r"@([\w一-鿿-]*)$")

#: 整条输入开头的 `@名字 正文`
AT_ADDRESS = re.compile(r"^@([\w一-鿿-]+)\s+(.*)$", re.DOTALL)


def split_address(text: str) -> tuple[str | None, str]:
    """把 `@名字 正文` 拆成 (收件人, 正文);没有 @ 前缀就返回 (None, 原文)。"""
    match = AT_ADDRESS.match(text.strip())
    if match is None:
        return None, text.strip()
    return match.group(1), match.group(2).strip()


def completion_prefix(text: str, cursor: int) -> str | None:
    """光标前如果正在写 `@xxx`,返回 xxx(可能是空串);否则 None。"""
    match = AT_PREFIX.search(text[:cursor])
    return match.group(1) if match else None


def matching_members(prefix: str, members: tuple[str, ...]) -> tuple[str, ...]:
    lowered = prefix.lower()
    return tuple(name for name in members if name.lower().startswith(lowered))


class ComposeInput(Input):
    """带 @ 补全与发言历史的输入框。"""

    class CandidatesChanged(Message):
        """候选变了(内容或选中项),让界面重画候选行。"""

        def __init__(self, compose: ComposeInput) -> None:
            super().__init__()
            self.compose = compose

    class DirectKey(Message):
        """成员直连态下需要原样转给 tmux 的非文本按键。"""

        def __init__(self, compose: ComposeInput, tmux_key: str, label: str) -> None:
            super().__init__()
            self.compose = compose
            self.tmux_key = tmux_key
            self.label = label

    def __init__(self, members: tuple[str, ...] = (), **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.members = members
        self.sent_history: list[str] = []
        self._history_cursor: int | None = None
        self.candidates: tuple[str, ...] = ()
        self.candidate_index = 0
        #: 当前补的是成员名还是命令名,决定候选行怎么显示
        self.candidate_kind = "member"
        #: 只在选中成员会话时由 ConsoleApp 打开,不能影响群聊焦点导航。
        self.direct_mode = False

    # --- 补全 -----------------------------------------------------------

    def _completion_source(self) -> tuple[str, tuple[str, ...], str] | None:
        """(已输入的前缀, 候选池, 类型);当前位置不该补全就返回 None。"""
        head = self.value[: self.cursor_position]
        if head.startswith("/") and " " not in head:
            return head[1:], COMMAND_NAMES, "command"
        prefix = completion_prefix(self.value, self.cursor_position)
        if prefix is None:
            return None
        return prefix, self.members, "member"

    def refresh_candidates(self) -> None:
        """按光标位置重算候选:`/` 开头补命令,`@` 开头补成员名。"""
        source = self._completion_source()
        if source is None:
            self.candidates, self.candidate_kind = (), "member"
        else:
            prefix, pool, kind = source
            self.candidates = matching_members(prefix, pool)
            self.candidate_kind = kind
        self.candidate_index = 0
        self.post_message(self.CandidatesChanged(self))

    @property
    def current_candidate(self) -> str | None:
        if not self.candidates:
            return None
        return self.candidates[self.candidate_index % len(self.candidates)]

    def cycle_candidate(self, step: int) -> None:
        if self.candidates:
            self.candidate_index = (self.candidate_index + step) % len(self.candidates)
            self.post_message(self.CandidatesChanged(self))

    def accept_candidate(self) -> bool:
        """把光标前的前缀补成完整的成员名 / 命令名,后面补一个空格。"""
        name = self.current_candidate
        source = self._completion_source()
        if name is None or source is None:
            return False
        prefix = source[0]
        start = self.cursor_position - len(prefix)
        self.value = f"{self.value[:start]}{name} {self.value[self.cursor_position:]}"
        self.cursor_position = start + len(name) + 1
        self.candidates = ()
        self.post_message(self.CandidatesChanged(self))
        return True

    # --- 发言历史 --------------------------------------------------------

    def remember(self, text: str) -> None:
        if text and (not self.sent_history or self.sent_history[-1] != text):
            self.sent_history.append(text)
        self._history_cursor = None

    def recall(self, step: int) -> bool:
        """`step=-1` 往更早翻,`step=+1` 往更近翻。翻到底回到空行。"""
        if not self.sent_history:
            return False
        if self._history_cursor is None:
            if step > 0:
                return False
            self._history_cursor = len(self.sent_history) - 1
        else:
            self._history_cursor += step
            if self._history_cursor < 0:
                self._history_cursor = 0
            elif self._history_cursor >= len(self.sent_history):
                self._history_cursor = None
                self.value = ""
                return True
        self.value = self.sent_history[self._history_cursor]
        self.cursor_position = len(self.value)
        return True

    # --- 按键 -----------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "shift+tab" and self.direct_mode:
            # Textual 默认把 Shift+Tab 当焦点反向循环;在直连输入框里把它
            # 转成 tmux 的 BTab,以支持 Claude Code 等终端程序的模式切换。
            self.post_message(self.DirectKey(self, "BTab", "Shift+Tab"))
            event.prevent_default()
            event.stop()
            return
        if self.direct_mode and not self.value and event.key in {"backspace", "delete"}:
            # Mac 键盘上标成 Delete 的键在终端里通常叫 Backspace；Fn+Delete
            # 才是 Forward Delete。输入框为空时本地已经没有字符可删，便把
            # 两种键分别按 tmux 的 BSpace / DC 原样交给成员 CLI。
            tmux_key, label = {
                "backspace": ("BSpace", "Delete/Backspace"),
                "delete": ("DC", "Forward Delete"),
            }[event.key]
            self.post_message(self.DirectKey(self, tmux_key, label))
            event.prevent_default()
            event.stop()
            return
        if event.key == "tab" and self.candidates:
            # 一次 Tab 只做一件事:没选过就先落定第一个,选过就换下一个
            self.accept_candidate()
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter" and self.candidates:
            source = self._completion_source()
            prefix = source[0] if source else ""
            if self.current_candidate != prefix:
                # 名字还没打全,这一下回车先补全;再按一次才发出去
                self.accept_candidate()
                event.prevent_default()
                event.stop()
                return
            self.candidates = ()
        if event.key in ("up", "down"):
            if self.candidates:
                self.cycle_candidate(-1 if event.key == "up" else 1)
            elif not self.recall(-1 if event.key == "up" else 1):
                return
            event.prevent_default()
            event.stop()
            return
        if event.key == "escape" and self.candidates:
            self.candidates = ()
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)
