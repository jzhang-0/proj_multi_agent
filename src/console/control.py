"""选中成员的控制操作与二次确认界面。"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bus.audit import AuditLog
from roster.lifecycle import Lifecycle
from tmuxctl import KeyInjector, ProcessController, Tmux
from workspace.session import session_for


@dataclass(frozen=True)
class ControlFeedback:
    action: str
    target: str
    changed: bool
    detail: str


Runner = Callable[..., subprocess.CompletedProcess[str]]

#: 直连输入时文本与 Enter 之间的间隔(秒)。太短会被 CLI 当成粘贴,
#: 那一下 Enter 就只在输入框里换行(cursor-agent 实测)。
SUBMIT_GAP_S = 0.15

# 实时键入的字符已经逐个到达成员终端；Enter 前只需留下一个很短的突发
# 边界，避免自动化快速键入时最后一个字符和 Enter 又被 CLI 合并成粘贴。
LIVE_SUBMIT_GAP_S = 0.01


class MemberController:
    """复用 TMX-005 与 ROS-003，并为每个动作落审计。"""

    def __init__(
        self,
        tmux: Tmux,
        lifecycle: Lifecycle,
        audit: AuditLog,
        *,
        process: ProcessController | None = None,
        runner: Runner = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tmux = tmux
        self.lifecycle = lifecycle
        self.audit = audit
        self.process = process or ProcessController(tmux)
        self.runner = runner
        self._sleep = sleeper

    def _record(self, feedback: ControlFeedback) -> ControlFeedback:
        self.audit.record_control(
            feedback.action,
            feedback.target,
            changed=feedback.changed,
            detail=feedback.detail,
        )
        return feedback

    def _failed(self, action: str, target: str, exc: Exception) -> None:
        self.audit.record_control(
            action,
            target,
            changed=False,
            detail=f"{type(exc).__name__}: {exc}",
        )

    def record_failure(self, action: str, target: str, exc: Exception) -> None:
        """记录控制动作在进入具体控制器前就失败的情况。"""
        self._failed(action, target, exc)

    def type_text(self, target: str, text: str) -> ControlFeedback:
        """把一行字直接键入成员终端并回车(总控台里的"单独说话")。

        不套 `[群消息] 来自 human:` 前缀——这是人在它自己的窗口里敲的一行,
        不是群聊流量。审计里记成 `type` 动作。

        **Enter 必须和文本分开发,中间留一口气**:文本和 Enter 挤在同一次 tmux
        调用里时,cursor-agent 这类 CLI 会把它当成一次粘贴,里面的换行只在输入
        框里换行、不提交(2026-08-16 实测:它的输入框里压着一条 14:58 的群消息
        和后来键入的 `11111`,补一个单独的 Enter 两条一起就提交了)。
        """
        try:
            self.tmux.send_keys(target, text, literal=True)
            self._sleep(SUBMIT_GAP_S)
            self.tmux.send_keys(target, "Enter")
            # 再确认一次:光标还压在这行字上就补 Enter(claude/codex 这类判得出来)
            outcome = KeyInjector(self.tmux).ensure_submitted(target, text)
            detail = text if outcome.submitted else f"{text}(可能没提交,已补 Enter)"
            return self._record(ControlFeedback("type", target, outcome.submitted, detail))
        except Exception as exc:
            self._failed("type", target, exc)
            raise

    def insert_text(self, target: str, text: str) -> None:
        """实时直连的一段可打印文字；不提交，也不为每个字符制造审计噪声。"""
        if not text:
            return
        try:
            self.tmux.send_keys(target, text, literal=True)
        except Exception as exc:
            self._failed("type", target, exc)
            raise

    def submit_live_text(self, target: str, text: str) -> ControlFeedback:
        """提交已经实时键入成员 composer 的文字，并把整次提交记为一条审计。"""
        try:
            self._sleep(LIVE_SUBMIT_GAP_S)
            self.tmux.send_keys(target, "Enter")
            outcome = KeyInjector(self.tmux).ensure_submitted(target, text) if text else None
            submitted = outcome is None or outcome.submitted
            detail = text or "Enter"
            if outcome is not None and not outcome.submitted:
                detail = f"{detail}(可能没提交,已补 Enter)"
            return self._record(ControlFeedback("type", target, submitted, detail))
        except Exception as exc:
            self._failed("type", target, exc)
            raise

    def press_key(self, target: str, key: str) -> ControlFeedback:
        """向成员终端发送一枚白名单内的非文本按键,并记录审计。"""
        if key not in {
            "Enter",
            "Tab",
            "BTab",
            "BSpace",
            "DC",
            "Up",
            "Down",
            "Left",
            "Right",
        }:
            raise ValueError(f"不允许透传的成员按键: {key}")
        try:
            self.tmux.send_keys(target, key)
            return self._record(ControlFeedback("key", target, True, key))
        except Exception as exc:
            self._failed("key", target, exc)
            raise

    def interrupt(self, target: str) -> ControlFeedback:
        try:
            result = self.process.interrupt(target)
            detail = "已发送 Escape + Ctrl-C" if result.changed else "目标已消失,未执行"
            return self._record(ControlFeedback("interrupt", target, result.changed, detail))
        except Exception as exc:
            self._failed("interrupt", target, exc)
            raise

    def terminate(self, target: str) -> ControlFeedback:
        try:
            result = self.process.terminate(target)
            detail = f"已向 CLI 进程 {result.pid} 发送 SIGTERM" if result.changed else "目标已消失"
            return self._record(ControlFeedback("terminate", target, result.changed, detail))
        except Exception as exc:
            self._failed("terminate", target, exc)
            raise

    def restart(self, target: str) -> ControlFeedback:
        try:
            result = self.lifecycle.restart(target)[0]
            return self._record(
                ControlFeedback("restart", target, result.changed, result.detail)
            )
        except Exception as exc:
            self._failed("restart", target, exc)
            raise

    def takeover(self, target: str) -> ControlFeedback:
        """前台 attach 到成员会话；调用方必须先挂起 Textual。"""
        try:
            if not self.tmux.has_session(target):
                return self._record(
                    ControlFeedback("takeover", target, False, "目标会话不存在")
                )
            session = session_for(self.tmux, target)
            argv = self.tmux.command_argv("attach-session", "-t", f"={session}")
            result = self.runner(argv, check=False)
            changed = result.returncode == 0
            detail = (
                "接管结束,已回到总控台"
                if changed
                else f"tmux attach 退出码 {result.returncode}"
            )
            return self._record(ControlFeedback("takeover", target, changed, detail))
        except Exception as exc:
            self._failed("takeover", target, exc)
            raise


class ConfirmControlScreen(ModalScreen[bool]):
    """终止/重启共用的危险操作确认框，默认焦点在取消。"""

    CSS = """
    ConfirmControlScreen {
        align: center middle;
        background: $background 60%;
    }
    #confirm-box {
        width: 54;
        height: 9;
        padding: 1 2;
        border: tall $warning;
        background: $panel;
    }
    #confirm-buttons {
        height: 3;
        align-horizontal: right;
    }
    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "确认", show=False),
        Binding("n", "cancel", "取消", show=False),
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, action_label: str, target: str) -> None:
        super().__init__()
        self.action_label = action_label
        self.target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                f"确认{self.action_label}成员 {self.target}？\n"
                "该操作会影响正在运行的任务。"
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("确认 (Y)", variant="error", id="confirm-yes")
                yield Button("取消 (N/Esc)", variant="primary", id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
