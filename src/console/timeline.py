"""群聊时间线:总线流量怎么变成一行行画面。

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
from dataclasses import dataclass
from typing import Any

from rich.text import Text

from bus import DeliveryResult
from bus.audit import AuditLog
from bus.sanitize import sanitize

#: 成员正文色。挑的是深浅色终端下都还看得清的中间调
MEMBER_PALETTE = (
    "#5fd7ff",
    "#ffaf5f",
    "#87ff87",
    "#ff87d7",
    "#d7d75f",
    "#87afff",
)

#: 两个保留名的固定颜色:人显眼,总线自己压低
FIXED_COLORS = {"human": "#ffd75f", "bus": "#9e9e9e"}

#: 结局 → (标记, 是否压暗)。拒收/失败都不是正常发言
OUTCOME_MARKS = {
    "delivered": ("✓", False),
    "shown": ("★", False),
    "deliver-failed": ("✗", True),
    "rejected": ("⊘", True),
    "malformed": ("☠", True),
    "pending": ("·", False),
}

#: 审计事件名 → 时间线结局。`deposit` 只说明消息进了队列,还不是结局
AUDIT_TO_OUTCOME = {
    "deposit": "pending",
    "deliver": "delivered",
    "deliver-failed": "deliver-failed",
    "rejected": "rejected",
    "malformed": "malformed",
}

#: `@名字`:字母数字下划线连字符,或中文
MENTION = re.compile(r"@[\w一-鿿-]+")

#: 回填多少条历史
HISTORY_LIMIT = 200


def member_color(name: str) -> str:
    """按名字算固定颜色。同一个名字永远同一个色。"""
    if name in FIXED_COLORS:
        return FIXED_COLORS[name]
    return MEMBER_PALETTE[zlib.crc32(name.encode("utf-8")) % len(MEMBER_PALETTE)]


@dataclass(frozen=True)
class TimelineEntry:
    """时间线上的一行:一条消息 + 它的结局。"""

    ts: str
    sender: str
    to: str
    text: str
    outcome: str = "pending"
    reason: str = ""

    @property
    def group(self) -> str:
        """分组键:精确到分钟。"""
        return self.ts[:16]

    @classmethod
    def from_result(cls, result: DeliveryResult) -> TimelineEntry:
        message = result.message
        if message is None:
            return cls("", "bus", "bus", result.path.name, str(result.outcome), result.detail)
        return cls(
            message.ts,
            message.sender,
            message.to,
            message.text,
            str(result.outcome),
            result.detail,
        )

    @classmethod
    def from_audit(cls, entry: dict[str, Any]) -> TimelineEntry:
        to = str(entry.get("to") or "?")
        outcome = AUDIT_TO_OUTCOME.get(str(entry.get("event", "")), "pending")
        # 审计日志把"送到人的屏幕上"也记成 deliver,时间线上要还原成 ★,
        # 免得历史和实时两种画法对同一条消息给出不同标记
        if outcome == "delivered" and to == "human":
            outcome = "shown"
        return cls(
            str(entry.get("ts", "")),
            str(entry.get("from") or "bus"),
            to,
            str(entry.get("preview", "")),
            outcome,
            str(entry.get("reason", "")),
        )


def history(audit: AuditLog, limit: int = HISTORY_LIMIT) -> list[TimelineEntry]:
    """从审计日志回填启动前的历史。

    同一条消息的多个事件合并成一行,结局取最后一个非 `deposit` 的事件。
    没有 `id` 的老消息(v0 形状的四字段 JSON)按 时间+收发+预览 当键,
    否则一条消息会在时间线上重复出现好几行。
    """
    merged: dict[str, TimelineEntry] = {}
    order: list[str] = []
    for raw in audit.entries():
        entry = TimelineEntry.from_audit(raw)
        raw_id = raw.get("id")
        fallback = f"{entry.ts}|{entry.sender}|{entry.to}|{entry.text}"
        key = raw_id if isinstance(raw_id, str) else fallback
        previous = merged.get(key)
        if previous is None:
            merged[key] = entry
            order.append(key)
            continue
        if entry.outcome != "pending":
            merged[key] = TimelineEntry(
                previous.ts,
                previous.sender,
                previous.to,
                previous.text,
                entry.outcome,
                entry.reason,
            )
    return [merged[key] for key in order][-limit:]


def group_header(group: str) -> Text:
    """时间戳分组的组头。"""
    return Text(f"── {group} " + "─" * 4, style="#5a5a5a")


def divider(label: str) -> Text:
    return Text(f"── {label} " + "─" * 4, style="#5a5a5a")


def render_entry(entry: TimelineEntry) -> Text:
    """把一行渲染成带样式的文本。上屏文本一律先清洗(BUS-005)。"""
    mark, dimmed = OUTCOME_MARKS.get(entry.outcome, ("?", True))
    line = Text(no_wrap=False)
    line.append(f"{mark} ", style="#9e9e9e" if dimmed else "#5a5a5a")
    line.append(sanitize(entry.sender), style=f"bold {member_color(entry.sender)}")
    line.append(" → ", style="#5a5a5a")
    line.append(sanitize(entry.to), style=member_color(entry.to))
    line.append(": ", style="#5a5a5a")
    line.append_text(highlight_mentions(sanitize(entry.text)))
    if entry.reason:
        line.append(f"  ({sanitize(entry.reason)})", style="#9e9e9e")
    if dimmed:
        line.stylize("dim")
    return line


def highlight_mentions(text: str) -> Text:
    """`@名字` 加亮,其余按普通正文。"""
    rendered = Text()
    cursor = 0
    for match in MENTION.finditer(text):
        rendered.append(text[cursor : match.start()])
        rendered.append(match.group(0), style="bold #ffffff on #005f87")
        cursor = match.end()
    rendered.append(text[cursor:])
    return rendered
