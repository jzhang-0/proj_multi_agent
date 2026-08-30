"""不依赖 UI 的终端画面结构识别。"""

from __future__ import annotations

import re

_HORIZONTAL_RULE = re.compile(r"^\s*[─━═-]{8,}\s*$")
_CODEX_PROMPT = re.compile(r"^\s*›(?:\s|$)")
_INPUT_TAIL_LINES = 8


def terminal_input_rows(screen_text: str) -> tuple[int, ...]:
    """返回 Claude/Codex 当前画面中可安全点击直连的输入区行号。"""
    lines = screen_text.splitlines()
    rules = [index for index, line in enumerate(lines) if _HORIZONTAL_RULE.fullmatch(line)]
    if len(rules) >= 2:
        lower = rules[-1]
        upper = rules[-2]
        if len(lines) - lower - 1 <= _INPUT_TAIL_LINES and upper + 1 < lower:
            return tuple(range(upper + 1, lower))

    start = max(0, len(lines) - _INPUT_TAIL_LINES)
    for index in range(len(lines) - 1, start - 1, -1):
        if _CODEX_PROMPT.match(lines[index]):
            return (index,)
    return ()
