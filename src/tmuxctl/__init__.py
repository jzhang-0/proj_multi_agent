"""tmux 控制层:总控台与成员终端之间全部 tmux 交互的唯一出口。"""

from tmuxctl.client import PaneInfo, Tmux
from tmuxctl.errors import (
    TmuxCommandError,
    TmuxError,
    TmuxNotFoundError,
    TmuxTimeoutError,
    TmuxVersionError,
)
from tmuxctl.inject import InjectOutcome, KeyInjector, inject_text, last_line_uncommitted
from tmuxctl.version import MIN_VERSION, TmuxVersion, probe

__all__ = [
    "MIN_VERSION",
    "InjectOutcome",
    "KeyInjector",
    "PaneInfo",
    "Tmux",
    "TmuxCommandError",
    "TmuxError",
    "TmuxNotFoundError",
    "TmuxTimeoutError",
    "TmuxVersion",
    "TmuxVersionError",
    "inject_text",
    "last_line_uncommitted",
    "probe",
]
