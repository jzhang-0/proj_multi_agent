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

import re

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

#: 往回翻一次跨多少行(PgUp/PgDn)
HISTORY_STEP = 10

#: 滚轮一格跨多少行;和 PgUp 一样跨十行的话手一抖就飞出去了
WHEEL_STEP = 3

#: 最多往回翻多少行(tmux 默认回滚区通常 2000 行)
HISTORY_LIMIT = 2000

# Claude 的输入区位于靠近底部的最后两条横线之间；Codex 的输入行以 `›`
# 开头。这里只认底部结构，不能因为历史正文里碰巧出现一个 `›` 就抢走鼠标。
_HORIZONTAL_RULE = re.compile(r"^\s*[─━═-]{8,}\s*$")
_CODEX_PROMPT = re.compile(r"^\s*›(?:\s|$)")
_INPUT_TAIL_LINES = 8


def terminal_input_rows(screen_text: str) -> tuple[int, ...]:
    """返回成员画面中可安全点击直连的输入区行号。

    支持当前名册里的两类 CLI：Claude 的双横线 composer 和 Codex 的底部
    `›` prompt。无法确认结构时宁可返回空，不把普通输出区误当输入框。
    """
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


class Mirror(Static):
    """成员终端画面。聚焦后 PgUp/PgDn 在成员自己的回滚区里翻。"""

    class LiveModeChanged(Message):
        """点击成员原生输入区后，实时直连态发生变化。"""

        def __init__(self, mirror: Mirror, active: bool) -> None:
            super().__init__()
            self.mirror = mirror
            self.active = active

    class LiveInput(Message):
        """实时直连态下需要按顺序交给成员终端的一次输入。"""

        def __init__(
            self,
            mirror: Mirror,
            kind: str,
            value: str = "",
            label: str = "",
        ) -> None:
            super().__init__()
            self.mirror = mirror
            self.kind = kind
            self.value = value
            self.label = label

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
        #: True 时键盘直接交给画面里的成员 CLI，不经过底部 ComposeInput。
        self.live_input = False

    @property
    def capture_start(self) -> int | None:
        """传给 `capture-pane -S` 的起点;不翻历史时不传。"""
        return -self.history_offset if self.history_offset else None

    def show_screen(self, ansi_text: str) -> None:
        """把带 ANSI 的画面渲染上去(颜色照搬成员终端里的样子)。

        **一行就是一行,不许换行**:抓来的是终端网格,成员那边已经排好版了。
        再让 Rich 按我们这边的宽度重排一次,长行就会折成两行、后半截还带一大
        段缩进,看着像凭空多出来的重复行(实测 agy 的中文行和带 emoji 的状态栏
        都这么裂过)。宽度不够就裁掉右边,别动版式。
        """
        rendered = Text.from_ansi(ansi_text, no_wrap=True, overflow="crop")
        self.screen_text = rendered.plain
        self.update(rendered)

    def notice(self, text: str) -> None:
        """画面还没到位时的提示。"""
        self.screen_text = text
        self.update(text)

    def set_live_input(self, active: bool) -> None:
        """切换实时直连态，并给 App 一个同步提示条的机会。"""
        active = bool(active and self.history_offset == 0)
        if self.live_input == active:
            return
        self.live_input = active
        self.set_class(active, "-live-input")
        self.post_message(self.LiveModeChanged(self, active))

    def click_hits_input(self, y: int) -> bool:
        """点击是否落在已识别的当前输入区；回滚画面永不允许激活。"""
        return self.history_offset == 0 and y in terminal_input_rows(self.screen_text)

    def on_click(self, event: events.Click) -> None:
        if event.button == 1 and self.click_hits_input(event.y):
            self.focus()
            self.set_live_input(True)
            event.stop()

    def on_blur(self, _event: events.Blur) -> None:
        self.set_live_input(False)

    async def _on_key(self, event: events.Key) -> None:
        """实时态只截获终端输入；全局 Ctrl+V、F5–F8 继续向 App 冒泡。"""
        if not self.live_input:
            return
        if event.key == "escape":
            self.set_live_input(False)
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter":
            self.post_message(self.LiveInput(self, "submit", label="Enter"))
            event.prevent_default()
            event.stop()
            return
        mapped = {
            "tab": ("Tab", "Tab"),
            "shift+tab": ("BTab", "Shift+Tab"),
            "backspace": ("BSpace", "Delete/Backspace"),
            "delete": ("DC", "Forward Delete"),
            "up": ("Up", "↑"),
            "down": ("Down", "↓"),
            "left": ("Left", "←"),
            "right": ("Right", "→"),
        }
        if event.key in mapped:
            tmux_key, label = mapped[event.key]
            self.post_message(self.LiveInput(self, "key", tmux_key, label))
            event.prevent_default()
            event.stop()
            return
        if event.is_printable and not any(
            event.key.startswith(prefix) for prefix in ("ctrl+", "meta+", "super+")
        ):
            self.post_message(self.LiveInput(self, "text", event.character or ""))
            event.prevent_default()
            event.stop()

    def scroll_history(self, lines: int) -> None:
        """往回翻 `lines` 行(负数往回走向当前画面)。"""
        if lines > 0:
            self.set_live_input(False)
        self.history_offset = max(0, min(HISTORY_LIMIT, self.history_offset + lines))

    def action_history_up(self) -> None:
        self.scroll_history(HISTORY_STEP)

    def action_history_down(self) -> None:
        self.scroll_history(-HISTORY_STEP)

    # 滚轮:不用先 Tab 到画面上,鼠标滚上去就能往回看
    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.scroll_history(WHEEL_STEP)
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.scroll_history(-WHEEL_STEP)
        event.stop()

    def action_history_top(self) -> None:
        self.set_live_input(False)
        self.history_offset = HISTORY_LIMIT

    def action_history_bottom(self) -> None:
        self.history_offset = 0
