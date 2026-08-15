"""窗格进程树与分级控制。"""

from __future__ import annotations

import os
import signal
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tmuxctl.client import PaneInfo
from tmuxctl.errors import TmuxCommandError

PS_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessInfo:
    """系统进程表中的一行。"""

    pid: int
    ppid: int
    pgid: int
    command: str


@dataclass(frozen=True)
class ProcessTree:
    """pane 根进程及递归后代。"""

    root: ProcessInfo
    descendants: tuple[ProcessInfo, ...]

    def children_of(self, pid: int) -> tuple[ProcessInfo, ...]:
        """返回指定 PID 的直接子进程。"""
        return tuple(process for process in self.descendants if process.ppid == pid)

    @property
    def cli_process(self) -> ProcessInfo:
        """返回 pane shell 的直接 CLI 子进程；没有子进程时返回根进程。"""
        children = self.children_of(self.root.pid)
        return max(children, key=lambda process: process.pid) if children else self.root


class ControlAction(StrEnum):
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILL = "kill"
    KILL_SESSION = "kill-session"


@dataclass(frozen=True)
class ControlResult:
    """控制动作结果；目标已消失时 ``changed=False`` 而不抛错。"""

    action: ControlAction
    target: str
    changed: bool
    pid: int | None = None


class ProcessTarget(Protocol):
    """进程控制器需要的最小 tmux 接口。"""

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]: ...

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None: ...

    def has_session(self, name: str) -> bool: ...

    def kill_session(self, name: str, *, missing_ok: bool = False) -> None: ...


def read_processes(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = PS_TIMEOUT_SECONDS,
) -> list[ProcessInfo]:
    """用系统 ``ps`` 读取 PID/PPID/PGID/命令。"""
    argv = ["ps", "-axo", "pid=,ppid=,pgid=,command="]
    process = runner(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if process.returncode != 0:
        raise TmuxCommandError(argv, process.returncode, process.stderr, process.stdout)

    rows = []
    for line in process.stdout.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) < 3:
            continue
        command = parts[3] if len(parts) == 4 else ""
        try:
            rows.append(ProcessInfo(int(parts[0]), int(parts[1]), int(parts[2]), command))
        except ValueError:
            continue
    return rows


def build_process_tree(root_pid: int, processes: Sequence[ProcessInfo]) -> ProcessTree | None:
    """从进程表构造指定根的递归后代树；根已退出则返回 ``None``。"""
    by_pid = {process.pid: process for process in processes}
    root = by_pid.get(root_pid)
    if root is None:
        return None

    children: dict[int, list[ProcessInfo]] = defaultdict(list)
    for process in processes:
        children[process.ppid].append(process)
    descendants = []
    pending = sorted(children[root_pid], key=lambda process: process.pid)
    seen = {root_pid}
    while pending:
        process = pending.pop(0)
        if process.pid in seen:
            continue
        seen.add(process.pid)
        descendants.append(process)
        pending.extend(sorted(children[process.pid], key=lambda child: child.pid))
    return ProcessTree(root, tuple(descendants))


def is_missing_target_error(error: TmuxCommandError) -> bool:
    """tmux 错误是否只表示目标/server 已消失。"""
    detail = f"{error.stderr}\n{error.stdout}".lower()
    markers = ("can't find", "no server running", "no sessions", "session not found")
    return any(marker in detail for marker in markers)


class ProcessController:
    """interrupt → terminate → kill 的幂等分级控制 API。"""

    def __init__(
        self,
        tmux: ProcessTarget,
        *,
        process_reader: Callable[[], Sequence[ProcessInfo]] = read_processes,
        signaler: Callable[[int, int], None] = os.kill,
    ) -> None:
        self._tmux = tmux
        self._process_reader = process_reader
        self._signaler = signaler

    def _pane(self, target: str) -> PaneInfo | None:
        try:
            panes = self._tmux.list_panes(target)
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return None
            raise
        if not panes:
            return None
        if len(panes) > 1:
            raise ValueError(f"目标 {target} 匹配多个窗格,请传 pane id")
        return panes[0]

    def pane_pid(self, target: str) -> int | None:
        """读取目标窗格根 PID；窗格已消失时返回 ``None``。"""
        pane = self._pane(target)
        return pane.pane_pid if pane is not None else None

    def process_tree(self, target: str) -> ProcessTree | None:
        """读取 pane 根进程与全部递归后代。"""
        pid = self.pane_pid(target)
        if pid is None:
            return None
        return build_process_tree(pid, self._process_reader())

    def interrupt(self, target: str) -> ControlResult:
        """软打断:依次发送 Escape 与 C-c；目标已消失视为成功的空操作。"""
        if self._pane(target) is None:
            return ControlResult(ControlAction.INTERRUPT, target, False)
        try:
            self._tmux.send_keys(target, "Escape", "C-c")
        except TmuxCommandError as exc:
            if is_missing_target_error(exc):
                return ControlResult(ControlAction.INTERRUPT, target, False)
            raise
        return ControlResult(ControlAction.INTERRUPT, target, True)

    def _signal_cli(self, target: str, action: ControlAction, signum: int) -> ControlResult:
        tree = self.process_tree(target)
        if tree is None:
            return ControlResult(action, target, False)
        pid = tree.cli_process.pid
        try:
            self._signaler(pid, signum)
        except ProcessLookupError:
            return ControlResult(action, target, False, pid)
        return ControlResult(action, target, True, pid)

    def terminate(self, target: str) -> ControlResult:
        """向 CLI 进程发送 SIGTERM。"""
        return self._signal_cli(target, ControlAction.TERMINATE, signal.SIGTERM)

    def kill(self, target: str) -> ControlResult:
        """向 CLI 进程发送 SIGKILL。"""
        return self._signal_cli(target, ControlAction.KILL, signal.SIGKILL)

    def kill_session(self, name: str) -> ControlResult:
        """强制结束整个 tmux 会话；重复调用安全。"""
        if not self._tmux.has_session(name):
            return ControlResult(ControlAction.KILL_SESSION, name, False)
        self._tmux.kill_session(name, missing_ok=True)
        return ControlResult(ControlAction.KILL_SESSION, name, True)
