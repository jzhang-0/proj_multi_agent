"""不可信文本的终端清洗。

安全边界(架构决策 §4):一切经总线到达的文本都是不可信输入。别人可以在
消息里塞终端转义序列——伪造窗口标题、清屏、把光标挪回去覆盖已有内容、
用 `\\r` 把前半句冲掉只留下后半句——从而在别人的终端上"画"出一句他没说
过的话。所以正文在两个入口各清洗一次:

- **投递入口**:`format_for_injection`,注入成员终端前;
- **上屏入口**:`format_for_screen`,画到总控台时间线前。

两处都清是因为两条路径不互相经过:投递走 tmux send-keys,上屏走 TUI 渲染,
将来任何一条路径改实现都不该把清洗漏掉。
"""

from __future__ import annotations

import re

from bus.message import Message

#: OSC:`ESC ]` 或 8-bit `\x9d` 开头,BEL / `ESC \` / ST 结束(伪造终端标题就走这里)
_OSC = re.compile(r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?")

#: CSI:`ESC [` 或 8-bit `\x9b` 开头(颜色、光标移动、清屏)
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?")

#: 其余单字符 ESC 序列(`ESC c` 全屏复位、`ESC ( B` 换字符集等)
_ESC_OTHER = re.compile(r"\x1b[ -/]*[0-~]?")

#: C1 控制字符
_C1 = re.compile(r"[\x80-\x9f]")

#: 这几个 C0 字符转成空格(保留词间分隔),其余 C0 与 DEL 直接删掉
_C0_TO_SPACE = {"\n", "\r", "\t", "\v", "\f"}


def sanitize(text: str) -> str:
    """剥掉 C0 控制字符、CSI 与 OSC 转义序列,换行等空白转成空格。

    顺序要紧:先按序列整段剥(序列里含可见字符,先删单字符会把序列打散成
    可见垃圾),再处理落单的控制字符。
    """
    cleaned = _OSC.sub("", text)
    cleaned = _CSI.sub("", cleaned)
    cleaned = _ESC_OTHER.sub("", cleaned)
    cleaned = _C1.sub("", cleaned)
    return "".join(
        " " if char in _C0_TO_SPACE else char
        for char in cleaned
        if char in _C0_TO_SPACE or (ord(char) >= 0x20 and char != "\x7f")
    )


def format_for_injection(message: Message) -> str:
    """投递入口:清洗后拼成注入成员终端的那一行(单行)。"""
    text = sanitize(message.text)
    sender = sanitize(message.sender)
    if message.kind == "ask" and message.id is not None:
        ask_id = sanitize(message.id)
        return (
            f"[群提问 ask {ask_id}] 来自 {sender}: {text}"
            f' —— 回复此提问,运行: ./msg --reply {ask_id} "你的答复"'
        )
    if message.kind == "reply" and message.reply_to is not None:
        ask_id = sanitize(message.reply_to)
        return f"[群回复 ask {ask_id}] 来自 {sender}: {text}"
    return f'[群消息] 来自 {sender}: {text} —— 如需回复,运行: ./msg {sender} "你的回复"'


def format_for_screen(message: Message) -> str:
    """上屏入口:清洗后拼成总控台时间线里的那一行。"""
    return (
        f"{sanitize(message.ts)}  {sanitize(message.sender)} -> "
        f"{sanitize(message.to)}: {sanitize(message.text)}"
    )
