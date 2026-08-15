"""成员详情:选中成员的终端画面镜像。

画面来自 TMX-004 的 `PaneSnapshotter`(`capture-pane -p -e`,带颜色,同一
窗格的高频请求会被合并),这里只负责三件事:

- **拉得够快**:活跃窗格 100ms 刷一次(产品定义的硬指标);
- **看不见就不拉**:详情栏折叠或窄屏让位时把定时器停掉,不白烧 CPU,
  也不去打扰 tmux;
- **能往上翻**:PgUp 把捕获起点往历史里推(`capture-pane -S`),翻的是
  成员终端真正的回滚区,不是我们缓存的那一屏。
"""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.widgets import Static

#: 往回翻一次跨多少行
HISTORY_STEP = 10

#: 最多往回翻多少行(tmux 默认回滚区通常 2000 行)
HISTORY_LIMIT = 2000


class Mirror(Static):
    """成员终端画面。聚焦后 PgUp/PgDn 在成员自己的回滚区里翻。"""

    can_focus = True

    BINDINGS = [
        Binding("pageup", "history_up", "看更早", show=False),
        Binding("pagedown", "history_down", "看更近", show=False),
        Binding("home", "history_top", "回滚区顶部", show=False),
        Binding("end", "history_bottom", "回到当前画面", show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]
        #: 往回翻了多少行;0 表示看的就是当前画面
        self.history_offset = 0
        #: 当前显示内容的纯文本(去掉样式),给测试和日志用
        self.screen_text = ""

    @property
    def capture_start(self) -> int | None:
        """传给 `capture-pane -S` 的起点;不翻历史时不传。"""
        return -self.history_offset if self.history_offset else None

    def show_screen(self, ansi_text: str) -> None:
        """把带 ANSI 的画面渲染上去(颜色照搬成员终端里的样子)。"""
        rendered = Text.from_ansi(ansi_text)
        self.screen_text = rendered.plain
        self.update(rendered)

    def notice(self, text: str) -> None:
        """画面还没到位时的提示。"""
        self.screen_text = text
        self.update(text)

    def action_history_up(self) -> None:
        self.history_offset = min(HISTORY_LIMIT, self.history_offset + HISTORY_STEP)

    def action_history_down(self) -> None:
        self.history_offset = max(0, self.history_offset - HISTORY_STEP)

    def action_history_top(self) -> None:
        self.history_offset = HISTORY_LIMIT

    def action_history_bottom(self) -> None:
        self.history_offset = 0
