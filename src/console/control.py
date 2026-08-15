"""选中成员的控制操作与二次确认界面。"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bus.audit import AuditLog
from roster.lifecycle import Lifecycle
from tmuxctl import ProcessController, Tmux


@dataclass(frozen=True)
class ControlFeedback:
    action: str
    target: str
    changed: bool
    detail: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


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
    ) -> None:
        self.tmux = tmux
        self.lifecycle = lifecycle
        self.audit = audit
        self.process = process or ProcessController(tmux)
        self.runner = runner

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
            argv = self.tmux.command_argv("attach-session", "-t", f"={target}")
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
