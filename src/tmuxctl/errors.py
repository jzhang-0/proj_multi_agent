"""tmux 控制层错误类型。"""

from __future__ import annotations

from collections.abc import Sequence


class TmuxError(Exception):
    """tmux 控制层错误基类。"""


class TmuxNotFoundError(TmuxError):
    """未找到 tmux 可执行文件。"""


class TmuxVersionError(TmuxError):
    """tmux 版本不满足最低要求,或输出无法解析。"""


class TmuxTimeoutError(TmuxError):
    """tmux 命令超过统一超时。"""


class TmuxCommandError(TmuxError):
    """tmux 命令以非零状态退出。"""

    def __init__(
        self,
        args: Sequence[str],
        returncode: int,
        stderr: str = "",
        stdout: str = "",
    ) -> None:
        self.args = list(args)
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        preview = (stderr or stdout).strip() or "(无输出)"
        super().__init__(
            f"tmux 命令失败 (exit {returncode}): {' '.join(self.args)}\n{preview}"
        )
