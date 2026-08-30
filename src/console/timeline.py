"""工作对话记录:总线流量怎么变成一行行画面。

三个约定:

- **发件人固定着色**:颜色由名字算出来(crc32 取模),同一个成员在任何
  一次运行、任何一台机器上都是同一个颜色,不随名册顺序变。
- **拒收是灰的**:被防环策略挡下的消息不是正常发言,整行压暗并带原因,
  一眼能和真实发言区分开。
- **按分钟分组**:同一分钟内的消息只在组头写一次时间,行内不再重复,
  省出来的宽度给正文。

历史回填读 `bus/log.jsonl`(BUS-008 的审计日志):一条消息可能有多条事件
(deposit → deliver/rejected),按 `id` 合并成一行,取最后的结局。
"""

from __future__ import annotations

import re
import zlib
from datetime import datetime

from rich.text import Text

from bus.sanitize import sanitize
from console.theme import tokens
from control.timeline import TimelineCategory, TimelineEntry, history
from control.vocabulary import TIMELINE_CATEGORY_LABELS, TIMELINE_OUTCOMES

__all__ = [
    "TimelineCategory",
    "TimelineEntry",
    "divider",
    "format_timestamp",
    "group_header",
    "highlight_mentions",
    "history",
    "member_color",
    "render_entry",
]

#: 结局 → (标记, 是否压暗)。拒收/失败都不是正常发言
OUTCOME_MARKS = TIMELINE_OUTCOMES

#: `@名字`:字母数字下划线连字符,或中文
MENTION = re.compile(r"@[\w一-鿿-]+")

CATEGORY_LABELS: dict[TimelineCategory, str] = {
    category: TIMELINE_CATEGORY_LABELS[str(category)] for category in TimelineCategory
}


def format_timestamp(value: str) -> str:
    """把账本 UTC ISO 时间转成本机时间；旧消息时间保持原样。"""
    if not value:
        return ""
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:19]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def member_color(name: str) -> str:
    """按名字算固定颜色:同一个名字在同一套主题里永远同一个色。

    颜色本身来自当前主题的 token(`console.theme`),换主题只换色号,
    "谁是几号色"这件事不变。
    """
    palette = tokens()
    if name == "human":
        return palette.human
    if name == "bus":
        return palette.bus
    return palette.members[zlib.crc32(name.encode("utf-8")) % len(palette.members)]


def group_header(group: str) -> Text:
    """时间戳分组的组头。"""
    return Text(f"── {group} " + "─" * 4, style=tokens().divider)


def divider(label: str) -> Text:
    return Text(f"── {label} " + "─" * 4, style=tokens().divider)


def render_entry(entry: TimelineEntry) -> Text:
    """把一行渲染成带样式的文本。上屏文本一律先清洗(BUS-005)。"""
    palette = tokens()
    mark, dimmed = OUTCOME_MARKS.get(entry.outcome, ("?", True))
    line = Text(no_wrap=False)
    line.append(f"{mark} ", style=palette.muted if dimmed else palette.divider)
    category = entry.resolved_category
    category_style = {
        TimelineCategory.HUMAN: palette.human,
        TimelineCategory.AI: palette.status["working"],
        TimelineCategory.TASK: palette.status["idle"],
        TimelineCategory.CONTROL: palette.muted,
    }[category]
    line.append(f"[{CATEGORY_LABELS[category]}] ", style=f"bold {category_style}")
    if entry.task_id:
        line.append(f"[{sanitize(entry.task_id)}] ", style=f"bold {palette.accent}")
    line.append(sanitize(entry.sender), style=f"bold {member_color(entry.sender)}")
    line.append(" → ", style=palette.divider)
    line.append(sanitize(entry.to), style=member_color(entry.to))
    line.append(": ", style=palette.divider)
    line.append_text(highlight_mentions(sanitize(entry.text)))
    if entry.attachment_count:
        line.append(f" [图片 {entry.attachment_count}]", style=f"bold {palette.accent}")
    if entry.reason:
        line.append(f"  ({sanitize(entry.reason)})", style=palette.muted)
    if dimmed:
        line.stylize("dim")
    return line


def highlight_mentions(text: str) -> Text:
    """`@名字` 加亮,其余按普通正文。"""
    rendered = Text()
    cursor = 0
    for match in MENTION.finditer(text):
        rendered.append(text[cursor : match.start()])
        palette = tokens()
        rendered.append(
            match.group(0), style=f"bold {palette.accent_text} on {palette.accent}"
        )
        cursor = match.end()
    rendered.append(text[cursor:])
    return rendered
