"""总控台 TUI 的应用骨架。

CON-001 只负责三件事:全屏起得来、`q` / Ctrl-C 干净退出(不碰任何成员
会话)、总线投递循环内嵌在里面转。三栏布局在 CON-002,时间线在 CON-003,
这里先用一块事件区把总线流量显示出来,证明循环真的在跑。

退出路径只做两件事:停投递循环、关应用。**绝不动 tmux**——成员会话是
成员自己的,总控台退出不该带走任何一个。
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, RichLog

from bus import BusPaths, DeliveryResult, Message
from bus.hub import tmux_deliver
from bus.sanitize import format_for_screen
from console.buspump import BusPump

#: 每种投递结果在界面上的标记(与 headless hub 的 ★/✓/✗ 保持一致)
MARKS = {
    "shown": "★",
    "delivered": "✓",
    "deliver-failed": "✗",
    "rejected": "⊘",
    "malformed": "☠",
}


class ConsoleApp(App[None]):
    """一台机器上多个 AI CLI 的群聊与指挥中心。"""

    TITLE = "总控台"
    SUB_TITLE = "本机 AI 群聊与指挥中心"

    CSS = """
    Screen {
        layers: base;
    }
    #events {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", priority=True, show=False),
    ]

    def __init__(
        self,
        paths: BusPaths | None = None,
        *,
        deliver: Callable[[Message], bool] = tmux_deliver,
    ) -> None:
        super().__init__()
        self.paths = (paths or BusPaths.resolve()).ensure()
        self.pump = BusPump(self.paths, self._on_result, deliver=deliver)

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="events", markup=False, wrap=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        events = self.query_one("#events", RichLog)
        events.write(f"[总控台] 总线目录 {self.paths.root}")
        events.write("[总控台] q 或 Ctrl-C 退出;退出不影响任何成员会话")
        self.pump.start()

    def on_unmount(self) -> None:
        self.pump.stop()

    # 投递线程回调 → 交回 UI 线程渲染
    def _on_result(self, result: DeliveryResult) -> None:
        self.call_from_thread(self.show_result, result)

    def show_result(self, result: DeliveryResult) -> None:
        """把一条投递结果显示出来(CON-003 会换成真正的时间线)。"""
        mark = MARKS.get(str(result.outcome), "?")
        if result.message is None:
            line = f"{mark} {result.path.name} — {result.detail}"
        else:
            detail = f"({result.detail})" if result.detail else ""
            line = f"{format_for_screen(result.message)}  {mark}{detail}"
        self.query_one("#events", RichLog).write(line)
