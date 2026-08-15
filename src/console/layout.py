"""终端里的宽度计算。

中英文混排的对齐必须按**显示宽度**算:CJK 字符占两列,按 `len()` 补空格
一定会错位(`/help` 那一列就这么错过一次)。所有列对齐、截断都走这里。
"""

from __future__ import annotations

import unicodedata


def display_width(text: str) -> int:
    """文本在终端里占几列。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(text: str, width: int) -> str:
    """右侧补空格到指定显示宽度(已经够宽就原样返回)。"""
    return text + " " * max(0, width - display_width(text))


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断,不会把一个宽字符劈成半个。"""
    if display_width(text) <= width:
        return text
    budget = max(0, width - display_width(ellipsis))
    kept: list[str] = []
    used = 0
    for char in text:
        step = display_width(char)
        if used + step > budget:
            break
        kept.append(char)
        used += step
    return "".join(kept) + ellipsis
