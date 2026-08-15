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
from tmuxctl.lifecycle import CrashEvent, CrashKind, CrashMonitor
from tmuxctl.process import (
    ControlAction,
    ControlResult,
    ProcessController,
    ProcessInfo,
    ProcessTree,
    build_process_tree,
    is_missing_target_error,
    read_processes,
)
from tmuxctl.snapshot import SNAPSHOT_INTERVAL_SECONDS, PaneSnapshot, PaneSnapshotter
from tmuxctl.version import MIN_VERSION, TmuxVersion, probe

__all__ = [
    "ControlAction",
    "ControlResult",
    "CrashEvent",
    "CrashKind",
    "CrashMonitor",
    "MIN_VERSION",
    "InjectOutcome",
    "KeyInjector",
    "PaneInfo",
    "PaneSnapshot",
    "PaneSnapshotter",
    "ProcessController",
    "ProcessInfo",
    "ProcessTree",
    "SNAPSHOT_INTERVAL_SECONDS",
    "Tmux",
    "TmuxCommandError",
    "TmuxError",
    "TmuxNotFoundError",
    "TmuxTimeoutError",
    "TmuxVersion",
    "TmuxVersionError",
    "inject_text",
    "is_missing_target_error",
    "build_process_tree",
    "last_line_uncommitted",
    "probe",
    "read_processes",
]
