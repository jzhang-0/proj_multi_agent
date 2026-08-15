"""总控台的自定义组件。"""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text
from textual.widgets import RichLog, Static

from console.members import STATUS_PRESENTATION, MemberCardSnapshot
from console.timeline import TimelineEntry, divider, group_header, render_entry


def render_member_card(snapshot: MemberCardSnapshot) -> Text:
    """两行成员卡片：图形+文字状态、队列数、最后活动。"""
    glyph, label, color = STATUS_PRESENTATION[snapshot.state]
    rendered = Text()
    rendered.append(f"{glyph} {label:<5} ", style=f"bold {color}")
    rendered.append(snapshot.name, style="bold")
    rendered.append(f"\n排队{snapshot.queued} · {snapshot.last_activity}", style="#9e9e9e")
    return rendered


class MemberCard(Static):
    """成员栏里固定两行、可原地刷新的卡片。"""

    def __init__(self, snapshot: MemberCardSnapshot, **kwargs: object) -> None:
        self.snapshot = snapshot
        super().__init__(render_member_card(snapshot), **kwargs)  # type: ignore[arg-type]

    def apply(self, snapshot: MemberCardSnapshot) -> None:
        self.snapshot = snapshot
        self.update(render_member_card(snapshot))


class Timeline(RichLog):
    """群聊时间线。

    两件 `RichLog` 不自带的事:按分钟写组头;人往上翻看历史时不要被新消息
    强行拽回底部(只有本来就贴着底的时候才跟着滚)。
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(markup=False, wrap=True, **kwargs)  # type: ignore[arg-type]
        self._group: str | None = None

    @property
    def sticking_to_bottom(self) -> bool:
        """当前视口是不是贴在最底部。"""
        return self.scroll_offset.y >= self.max_scroll_y - 1

    def _emit(self, renderable: Text, *, stick: bool) -> None:
        self.write(renderable, scroll_end=stick)

    def note(self, text: str) -> None:
        """总控台自己说的话(不是总线流量)。"""
        self._emit(Text(text, style="#5a5a5a"), stick=self.sticking_to_bottom)

    def add(self, entry: TimelineEntry) -> None:
        """追加一条流量,必要时先写组头。"""
        stick = self.sticking_to_bottom
        if entry.group and entry.group != self._group:
            self._group = entry.group
            self._emit(group_header(entry.group), stick=stick)
        self._emit(render_entry(entry), stick=stick)

    def backfill(self, entries: Iterable[TimelineEntry]) -> int:
        """回填启动前的历史,末尾画一条分界线。返回回填条数。"""
        count = 0
        for entry in entries:
            self.add(entry)
            count += 1
        if count:
            self._emit(divider(f"以上 {count} 条来自 bus/log.jsonl"), stick=True)
            self._group = None
        return count
