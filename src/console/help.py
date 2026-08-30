"""总控台快捷键帮助面板。"""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


@dataclass(frozen=True)
class Shortcut:
    keys: str
    description: str


SHORTCUT_GROUPS: tuple[tuple[str, tuple[Shortcut, ...]], ...] = (
    (
        "导航与焦点",
        (
            Shortcut("Tab / Shift+Tab", "工作对话与非输入框焦点时在各区域间循环"),
            Shortcut("↑ / ↓", "选择任务、工作对话或成员；输入时选候选项或翻历史"),
            Shortcut("对话分类 ← / →", "聚焦分类栏后切换 human、AI、任务与终端控制"),
            Shortcut("Esc / F2", "回到工作对话记录"),
            Shortcut("F3", "打开任务看板；/task T-001 可直达详情"),
            Shortcut("PgUp / PgDn", "翻当前画面；MacBook 对应 Fn+↑ / Fn+↓"),
            Shortcut("Ctrl+↑ / Ctrl+↓", "逐行回看；macOS 若占用请用 Fn+↑ / Fn+↓"),
            Shortcut("Home / End", "跳到当前区域最早 / 最新位置"),
        ),
    ),
    (
        "发言与命令",
        (
            Shortcut("@成员", "输入 @ 即列出成员；Tab / ↑↓ 选择，回车落定"),
            Shortcut("Ctrl+V", "粘贴系统剪贴板图片，可随文字或单独发送"),
            Shortcut("/ 命令", "斜杠命令补全,含 /workspace；/task 打开任务"),
            Shortcut("Enter", "接受补全，再按一次发送或执行"),
            Shortcut("成员会话里直接打字", "不带 @ 就是直接键入该成员终端，等于在它窗口里敲"),
            Shortcut("点击成员输入区", "进入实时直连；按键在成员画面即时回显，Esc 退出"),
            Shortcut("直连空 ↑ / ↓", "透传 Up / Down，操作成员终端里的交互菜单"),
            Shortcut("直连空 Del", "只删除成员当前未提交草稿；已提交消息不可编辑"),
            Shortcut("直连空 ↵", "透传 Enter 给成员当前输入框"),
            Shortcut("直连 Shift+Tab", "透传 BTab 给成员终端"),
        ),
    ),
    (
        "成员控制",
        (
            Shortcut("F5", "打断选中成员"),
            Shortcut("F6", "终止选中成员（Y 确认 / N 取消）"),
            Shortcut("F7", "重启选中成员（Y 确认 / N 取消）"),
            Shortcut("F8", "全屏接管选中成员，退出 attach 后返回"),
        ),
    ),
    (
        "全局",
        (
            Shortcut("? / F1", "打开本面板（正在输入文字时用 F1）"),
            Shortcut("T", "切换深色 / 浅色主题"),
            Shortcut("Q / Ctrl+C", "退出总控台，不停止成员会话"),
        ),
    ),
)

KEY_COLUMN_WIDTH = 20


def render_shortcuts() -> Text:
    """把声明式快捷键表渲染成窄屏也能阅读的文本。"""
    rendered = Text()
    for group_index, (title, shortcuts) in enumerate(SHORTCUT_GROUPS):
        if group_index:
            rendered.append("\n")
        rendered.append(f"{title}\n", style="bold")
        for shortcut in shortcuts:
            rendered.append("  ")
            rendered.append(shortcut.keys, style="bold")
            rendered.append(" " * max(1, KEY_COLUMN_WIDTH - cell_len(shortcut.keys)))
            rendered.append(f"{shortcut.description}\n")
    return rendered


class ShortcutHelpScreen(ModalScreen[None]):
    """`?` / F1 打开的键盘帮助，关闭后恢复原焦点。"""

    CSS = """
    ShortcutHelpScreen {
        align: center middle;
        background: $background 65%;
    }
    #shortcut-help-box {
        width: 76;
        max-width: 96%;
        height: 25;
        max-height: 92%;
        padding: 1 2;
        border: tall $accent;
        background: $panel;
    }
    #shortcut-help-title {
        height: 2;
        content-align: center middle;
        text-style: bold;
        color: $accent;
    }
    #shortcut-help-scroll {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    #shortcut-help-close {
        width: 24;
        height: 3;
        align-horizontal: center;
    }
    """

    BINDINGS = [
        Binding("question_mark", "close_help", "关闭", show=False),
        Binding("f1", "close_help", "关闭", show=False),
        Binding("escape", "close_help", "关闭", show=False),
        Binding("q", "close_help", "关闭", show=False),
        Binding("pageup", "scroll_help('page_up')", "上翻", show=False),
        Binding("pagedown", "scroll_help('page_down')", "下翻", show=False),
        Binding("home", "scroll_help('home')", "顶部", show=False),
        Binding("end", "scroll_help('end')", "底部", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="shortcut-help-box"):
            yield Static("⌨ 快捷键帮助 · PgUp/PgDn 滚动", id="shortcut-help-title")
            with VerticalScroll(id="shortcut-help-scroll"):
                yield Static(render_shortcuts(), id="shortcut-help-content")
            yield Button("关闭 (? / Esc)", variant="primary", id="shortcut-help-close")

    def on_mount(self) -> None:
        self.query_one("#shortcut-help-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close_help(self) -> None:
        self.dismiss()

    def action_scroll_help(self, direction: str) -> None:
        scroll = self.query_one("#shortcut-help-scroll", VerticalScroll)
        {
            "page_up": scroll.scroll_page_up,
            "page_down": scroll.scroll_page_down,
            "home": scroll.scroll_home,
            "end": scroll.scroll_end,
        }[direction](animate=False)
