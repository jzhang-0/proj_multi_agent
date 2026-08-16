"""总控台的自定义组件。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import RichLog, Static

from console.layout import pad
from console.members import STATUS_PRESENTATION, MemberCardSnapshot
from console.theme import tokens
from console.timeline import TimelineEntry, divider, group_header, render_entry


def render_member_card(snapshot: MemberCardSnapshot) -> Text:
    """两行成员卡片：图形+文字状态、队列数、最后活动。"""
    glyph, label, color = STATUS_PRESENTATION[snapshot.state]
    rendered = Text()
    # 列宽按显示宽度补:成员名可能是中文,按字符数补会把第二行顶歪
    rendered.append(f"{glyph} {pad(label, 5)} ", style=f"bold {color}")
    rendered.append(snapshot.name, style="bold")
    rendered.append(f"\n排队{snapshot.queued} · {snapshot.last_activity}", style=tokens().muted)
    return rendered


class MemberCard(Static):
    """成员栏里固定两行、可原地刷新的卡片。"""

    def __init__(self, snapshot: MemberCardSnapshot, **kwargs: object) -> None:
        self.snapshot = snapshot
        super().__init__(render_member_card(snapshot), **kwargs)  # type: ignore[arg-type]

    def apply(self, snapshot: MemberCardSnapshot) -> None:
        self.snapshot = snapshot
        self.update(render_member_card(snapshot))


def render_conversation_card(unread: int, watching: bool) -> Text:
    """会话列表第一项:群聊。两行,和成员卡片一样高一样对齐。"""
    palette = tokens()
    rendered = Text()
    rendered.append("≡ ", style=f"bold {palette.human}")
    rendered.append("群聊时间线", style="bold")
    if unread:
        rendered.append(f"\n未读 {unread} 条", style=f"bold {palette.accent}")
    elif watching:
        rendered.append("\n在看", style=palette.muted)
    else:
        rendered.append("\n全部流量", style=palette.muted)
    return rendered


class ConversationCard(Static):
    """群聊那一项;未读数原地刷新。"""

    def __init__(self, **kwargs: object) -> None:
        self.unread = 0
        self.watching = True
        super().__init__(render_conversation_card(0, True), **kwargs)  # type: ignore[arg-type]

    def apply(self, unread: int, *, watching: bool) -> None:
        self.unread, self.watching = unread, watching
        self.update(render_conversation_card(unread, watching))


@dataclass(frozen=True)
class Divider:
    """时间线上的一条分界线(回填历史与实时流量之间那条)。

    和普通提示分开记,重画时才知道它该画成分界线而不是一行字。
    """

    text: str


class Timeline(RichLog):
    """群聊时间线。

    三件 `RichLog` 不自带的事:按分钟写组头;人往上翻看历史时不要被新消息
    强行拽回底部(只有本来就贴着底的时候才跟着滚);宽度变了(切面板、详情
    栏让位、终端拉伸)要按新宽度整体重排——`RichLog` 的行是写进去时就定死
    的,不重排就会横向溢出或留一大截空白。
    """

    def __init__(self, **kwargs: object) -> None:
        # min_width 默认 78:左栏只有四十几列时,行会按 78 排完再被裁掉右半截
        # (实测消息尾巴直接消失),这里让宽度完全跟着视口走。
        super().__init__(markup=False, wrap=True, min_width=1, **kwargs)  # type: ignore[arg-type]
        self._group: str | None = None
        #: 已上屏的内容。换主题要按新颜色重画,所以得留着原始数据,
        #: 不能只留渲染完的行
        self._history: list[TimelineEntry | Divider | str] = []
        #: 当前这批行是按多宽排的;0 表示还没上过屏
        self._laid_out_width = 0
        #: 被别的面板盖住期间写过内容——那些行是按 `min_width` 排的,
        #: 重新露头时哪怕宽度没变也得补排一次
        self._wrote_blind = False
        self._relaying = False

    def on_resize(self, event: object) -> None:
        super().on_resize(event)  # type: ignore[arg-type]
        width = self.scrollable_content_region.width
        if not width or self._relaying:
            return
        if width == self._laid_out_width and not self._wrote_blind:
            return
        self._laid_out_width = width
        if self._history:
            self._relaying = True
            try:
                self.rerender()
            finally:
                self._relaying = False
        self._wrote_blind = False

    @property
    def sticking_to_bottom(self) -> bool:
        """当前视口是不是贴在最底部。"""
        return self.scroll_offset.y >= self.max_scroll_y - 1

    def _emit(self, renderable: Text, *, stick: bool) -> None:
        if not self.display or not self.size.width:
            self._wrote_blind = True
        self.write(renderable, scroll_end=stick)

    def note(self, text: str) -> None:
        """总控台自己说的话(不是总线流量)。"""
        self._history.append(text)
        self._emit(Text(text, style=tokens().divider), stick=self.sticking_to_bottom)

    def add(self, entry: TimelineEntry) -> None:
        """追加一条流量,必要时先写组头。"""
        self._history.append(entry)
        stick = self.sticking_to_bottom
        if entry.group and entry.group != self._group:
            self._group = entry.group
            self._emit(group_header(entry.group), stick=stick)
        self._emit(render_entry(entry), stick=stick)

    def _draw_divider(self, text: str) -> None:
        self._emit(divider(text), stick=True)
        self._group = None  # 分界线之后重新写组头

    def rerender(self) -> None:
        """按当前主题(或新宽度)把已有内容整体重画一遍。"""
        history = list(self._history)
        self.clear()
        self._group = None
        self._history = []
        for item in history:
            if isinstance(item, Divider):
                self._history.append(item)
                self._draw_divider(item.text)
            elif isinstance(item, str):
                self.note(item)
            else:
                self.add(item)

    def backfill(self, entries: Iterable[TimelineEntry]) -> int:
        """回填启动前的历史,末尾画一条分界线。返回回填条数。"""
        count = 0
        for entry in entries:
            self.add(entry)
            count += 1
        if count:
            marker = Divider(f"以上 {count} 条来自 bus/log.jsonl")
            self._history.append(marker)
            self._draw_divider(marker.text)
        return count
