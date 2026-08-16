"""tmux 控制层:总控台与成员终端之间全部 tmux 交互的唯一出口。"""

from tmuxctl.activity import (
    ActivityMonitor,
    ActivitySnapshot,
    ActivityState,
    ActivityTracker,
)
from tmuxctl.client import PaneInfo, Tmux
from tmuxctl.errors import (
    TmuxCommandError,
    TmuxError,
    TmuxNotFoundError,
    TmuxTimeoutError,
    TmuxVersionError,
)
from tmuxctl.inject import (
    InjectOutcome,
    KeyInjector,
    SubmitOutcome,
    cursor_line_holds,
    inject_text,
    last_line_uncommitted,
)
from tmuxctl.lifecycle import CrashEvent, CrashKind, CrashMonitor
from tmuxctl.output import PaneOutputStream, decode_control_data, subscribe_pane
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
    "ActivityMonitor",
    "ActivitySnapshot",
    "ActivityState",
    "ActivityTracker",
    "ControlAction",
    "ControlResult",
    "CrashEvent",
    "CrashKind",
    "CrashMonitor",
    "MIN_VERSION",
    "InjectOutcome",
    "KeyInjector",
    "PaneInfo",
    "PaneOutputStream",
    "PaneSnapshot",
    "PaneSnapshotter",
    "ProcessController",
    "ProcessInfo",
    "ProcessTree",
    "SNAPSHOT_INTERVAL_SECONDS",
    "SubmitOutcome",
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
    "cursor_line_holds",
    "decode_control_data",
    "last_line_uncommitted",
    "probe",
    "read_processes",
    "subscribe_pane",
]
