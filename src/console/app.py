"""总控台 TUI:三栏布局与应用骨架。

布局(产品定义里的那张图):

```
┌─成员─────┬─群聊时间线──────────┬─成员详情(可折叠)─┐
│ claude   │ 12:01 human→claude  │ [选中成员的画面]  │
├──────────┴─────────────────────┴───────────────────┤
│ > @claude 修一下登录页的bug_                       │
└────────────────────────────────────────────────────┘
```

三条规则:详情栏默认折叠,选中成员才展开;终端宽度不够(< 100 列)时
详情栏自动让位,把宽度还给时间线;最小可用尺寸 80×24,成员栏和时间线
在这个尺寸下都还完整可读。

退出路径只做两件事:停投递循环、关应用。**绝不动 tmux**——成员会话是
成员自己的,总控台退出不该带走任何一个。
"""

from __future__ import annotations

from collections.abc import Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from bus import BusPaths, DeliveryResult, Message, deposit
from bus.audit import AuditLog
from bus.hub import tmux_deliver
from console.buspump import BusPump
from console.compose import ComposeInput, split_address
from console.members import member_names
from console.timeline import TimelineEntry, history
from console.widgets import Timeline

#: 低于这个列数就不再显示详情栏:80 列时成员栏 + 时间线已经占满,
#: 再挤一栏会把时间线压到没法读
DETAIL_MIN_WIDTH = 100

#: 最小可用尺寸(产品定义)
MIN_SIZE = (80, 24)


class ConsoleApp(App[None]):
    """一台机器上多个 AI CLI 的群聊与指挥中心。"""

    TITLE = "总控台"
    SUB_TITLE = "本机 AI 群聊与指挥中心"

    CSS = """
    #body {
        height: 1fr;
    }
    #members {
        width: 18;
        border-right: solid $primary-darken-2;
        background: $surface;
    }
    #members > ListItem {
        padding: 0 1;
    }
    #center {
        width: 1fr;
    }
    #timeline {
        height: 1fr;
        padding: 0 1;
    }
    #compose {
        height: 3;
        border: tall $primary-darken-2;
    }
    #suggestions {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        display: none;
    }
    #detail {
        width: 34;
        border-left: solid $primary-darken-2;
        padding: 0 1;
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", priority=True, show=False),
        Binding("escape", "clear_selection", "收起详情"),
        Binding("pageup", "timeline_scroll('page_up')", "上翻", show=False),
        Binding("pagedown", "timeline_scroll('page_down')", "下翻", show=False),
        Binding("home", "timeline_scroll('home')", "回到最早", show=False),
        Binding("end", "timeline_scroll('end')", "回到最新", show=False),
    ]

    def __init__(
        self,
        paths: BusPaths | None = None,
        *,
        deliver: Callable[[Message], bool] = tmux_deliver,
        members: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self.paths = (paths or BusPaths.resolve()).ensure()
        self.pump = BusPump(self.paths, self._on_result, deliver=deliver)
        self.members: tuple[str, ...] = members if members is not None else member_names()
        self.selected_member: str | None = None
        #: 上一个对话对象:不写 @ 前缀时默认发给它
        self.last_target: str | None = None

    # --- 布局 -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            # initial_index=None:一起来不要自动高亮第一个成员,
            # 否则"详情栏默认折叠"就被 ListView 自己破坏了
            yield ListView(
                *(ListItem(Label(name), id=f"member-{name}") for name in self.members),
                id="members",
                initial_index=None,
            )
            with Vertical(id="center"):
                yield Timeline(id="timeline")
                yield Static("", id="suggestions", markup=False)
                yield ComposeInput(
                    members=self.members,
                    placeholder="@名字 说点什么,回车发送",
                    id="compose",
                )
            yield Static("", id="detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        timeline = self.query_one("#timeline", Timeline)
        timeline.note(f"[总控台] 总线目录 {self.paths.root}")
        timeline.backfill(history(AuditLog(self.paths)))
        timeline.note("[总控台] ↑↓ 选成员,PgUp/PgDn 翻时间线,Esc 收起详情;q 或 Ctrl-C 退出")
        # 焦点先给成员栏:CON-002 的交互就是选成员。输入框的焦点规则在 CON-004/012。
        self.query_one("#members", ListView).focus()
        self.pump.start()

    def on_unmount(self) -> None:
        self.pump.stop()

    # --- 详情栏的展开/让位 ------------------------------------------------

    @property
    def detail_visible(self) -> bool:
        return bool(self.query_one("#detail", Static).display)

    def _sync_detail(self, width: int | None = None) -> None:
        """详情栏只在"选中了成员"且"宽度够"时出现,两个条件缺一不可。

        `width` 显式传进来是因为处理 Resize 事件时 `self.size` 还是旧值,
        必须用事件里带的新尺寸判断。
        """
        detail = self.query_one("#detail", Static)
        wide_enough = (self.size.width if width is None else width) >= DETAIL_MIN_WIDTH
        detail.display = self.selected_member is not None and wide_enough
        if detail.display:
            detail.update(
                f"成员详情 · {self.selected_member}\n\n"
                "终端画面镜像在 CON-006;这里先占位,证明详情栏能按选中展开。"
            )

    def select_member(self, name: str | None) -> None:
        self.selected_member = name
        self._sync_detail()

    def action_clear_selection(self) -> None:
        self.select_member(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None or item.id is None:
            return
        self.select_member(item.id.removeprefix("member-"))

    def on_resize(self, event: events.Resize) -> None:
        self._sync_detail(width=event.size.width)

    # --- 输入框 ----------------------------------------------------------

    def on_input_changed(self, event: ComposeInput.Changed) -> None:
        if event.input.id == "compose":
            self.query_one("#compose", ComposeInput).refresh_candidates()

    def on_compose_input_candidates_changed(self, event: ComposeInput.CandidatesChanged) -> None:
        self._sync_suggestions()

    def on_input_submitted(self, event: ComposeInput.Submitted) -> None:
        if event.input.id == "compose":
            self.send_from_input(event.value)

    def _sync_suggestions(self) -> None:
        """候选行:只在有候选时占一行,当前候选加方括号。"""
        compose = self.query_one("#compose", ComposeInput)
        row = self.query_one("#suggestions", Static)
        row.display = bool(compose.candidates)
        if not compose.candidates:
            return
        current = compose.current_candidate
        shown = " ".join(f"[{name}]" if name == current else name for name in compose.candidates)
        row.update(f"Tab/↑↓ 选择:{shown}")

    def _update_placeholder(self) -> None:
        compose = self.query_one("#compose", ComposeInput)
        if self.last_target is None:
            compose.placeholder = "@名字 说点什么,回车发送"
        else:
            compose.placeholder = f"回车发给 {self.last_target}(@名字 可改收件人)"

    def send_from_input(self, raw: str) -> None:
        """把输入框里的一行发上总线。发件人永远是 human。"""
        compose = self.query_one("#compose", ComposeInput)
        timeline = self.query_one("#timeline", Timeline)
        addressed, text = split_address(raw)
        target = addressed or self.last_target
        if not text:
            return
        if target is None:
            timeline.note("[总控台] 还没有对话对象,先用 @名字 指定收件人")
            return
        deposit(Message.create(target, text, sender="human"), self.paths)
        compose.remember(raw)
        compose.value = ""
        compose.candidates = ()
        self._sync_suggestions()
        self.last_target = target
        self._update_placeholder()

    # --- 总线流量 --------------------------------------------------------

    def _on_result(self, result: DeliveryResult) -> None:
        """投递线程回调 → 交回 UI 线程渲染。"""
        self.call_from_thread(self.show_result, result)

    def show_result(self, result: DeliveryResult) -> None:
        """把一条投递结果追加到时间线。"""
        self.query_one("#timeline", Timeline).add(TimelineEntry.from_result(result))

    # --- 滚动回看 --------------------------------------------------------

    def action_timeline_scroll(self, direction: str) -> None:
        timeline = self.query_one("#timeline", Timeline)
        {
            "page_up": timeline.scroll_page_up,
            "page_down": timeline.scroll_page_down,
            "home": timeline.scroll_home,
            "end": timeline.scroll_end,
        }[direction]()
