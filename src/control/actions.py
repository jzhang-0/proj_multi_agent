"""成员打断、终止、重启与控制审计的共享编排。"""

from __future__ import annotations

from dataclasses import dataclass

from bus.audit import AuditLog
from roster.lifecycle import Lifecycle
from tmuxctl import ProcessController, Tmux


@dataclass(frozen=True)
class ControlFeedback:
    action: str
    target: str
    changed: bool
    detail: str


class MemberActionController:
    """只编排可由 TUI/Web 共用的三项成员动作及审计。"""

    def __init__(
        self,
        tmux: Tmux,
        lifecycle: Lifecycle,
        audit: AuditLog,
        *,
        process: ProcessController | None = None,
    ) -> None:
        self.tmux = tmux
        self.lifecycle = lifecycle
        self.audit = audit
        self.process = process or ProcessController(tmux)

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
