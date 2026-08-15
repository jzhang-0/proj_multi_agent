"""tmux 命令的类型化封装。其他模块不应直接拼装 tmux 命令行。"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tmuxctl.errors import TmuxCommandError, TmuxNotFoundError, TmuxTimeoutError
from tmuxctl.version import DEFAULT_TIMEOUT, TmuxVersion, probe

_PANE_FORMAT = (
    "#{session_name}\t#{window_index}\t#{pane_index}\t"
    "#{pane_id}\t#{pane_pid}\t#{pane_current_command}"
)


@dataclass(frozen=True)
class PaneInfo:
    """`list-panes` 解析出的窗格信息。"""

    session_name: str
    window_index: int
    pane_index: int
    pane_id: str
    pane_pid: int
    current_command: str


class Tmux:
    """对 has-session / new-session / kill-session / send-keys / capture-pane / list-panes 的封装。

    构造时探测 tmux ≥ 3.2。所有命令走统一超时与错误类型。
    `has-session` / `kill-session` 使用 `=name` 精确匹配; `send-keys` 使用普通会话名
    (tmux 不接受 `send-keys -t =name`)。
    """

    def __init__(
        self,
        *,
        tmux: str = "tmux",
        timeout: float = DEFAULT_TIMEOUT,
        socket_name: str | None = None,
        socket_path: str | None = None,
    ) -> None:
        self._tmux = tmux
        self._timeout = timeout
        self._socket_name = socket_name
        self._socket_path = socket_path
        self.version: TmuxVersion = probe(tmux, timeout=timeout)

    def _argv(self, *args: str) -> list[str]:
        cmd = [self._tmux]
        if self._socket_path:
            cmd.extend(["-S", self._socket_path])
        elif self._socket_name:
            cmd.extend(["-L", self._socket_name])
        cmd.extend(args)
        return cmd

    def _run(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = self._argv(*args)
        limit = self._timeout if timeout is None else timeout
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
            )
        except FileNotFoundError as exc:
            raise TmuxNotFoundError(f"未找到 tmux 可执行文件: {self._tmux}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TmuxTimeoutError(f"tmux 命令超时 ({limit}s): {' '.join(argv)}") from exc
        if check and proc.returncode != 0:
            raise TmuxCommandError(argv, proc.returncode, proc.stderr, proc.stdout)
        return proc

    def has_session(self, name: str) -> bool:
        """会话是否存在。使用 `=name` 精确匹配,找不到时返回 False 而不抛错。"""
        proc = self._run("has-session", "-t", f"={name}", check=False)
        return proc.returncode == 0

    def new_session(
        self,
        name: str,
        *,
        command: str | Sequence[str] | None = None,
        detached: bool = True,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        window_name: str | None = None,
    ) -> None:
        """创建会话。会话已存在时抛出 TmuxCommandError。"""
        args: list[str] = ["new-session"]
        if detached:
            args.append("-d")
        args.extend(["-s", name])
        if window_name is not None:
            args.extend(["-n", window_name])
        if cwd is not None:
            args.extend(["-c", cwd])
        if env:
            for key, value in env.items():
                args.extend(["-e", f"{key}={value}"])
        if command is not None:
            if isinstance(command, str):
                args.append(command)
            else:
                args.extend(command)
        self._run(*args)

    def kill_session(self, name: str, *, missing_ok: bool = False) -> None:
        """结束会话。使用 `=name` 精确匹配。"""
        proc = self._run("kill-session", "-t", f"={name}", check=not missing_ok)
        if missing_ok and proc.returncode != 0 and self.has_session(name):
            raise TmuxCommandError(
                self._argv("kill-session", "-t", f"={name}"),
                proc.returncode,
                proc.stderr,
                proc.stdout,
            )

    def send_keys(self, target: str, *keys: str, literal: bool = False) -> None:
        """向目标窗格注入按键。target 用普通会话名或 pane id,不要加 `=`。"""
        args = ["send-keys", "-t", target]
        if literal:
            args.append("-l")
        args.extend(keys)
        self._run(*args)

    def capture_pane(
        self,
        target: str,
        *,
        escape: bool = False,
        start: int | str | None = None,
        end: int | str | None = None,
    ) -> str:
        """截取窗格画面。escape=True 相当于 `-e`(保留颜色)。"""
        args = ["capture-pane", "-p", "-t", target]
        if escape:
            args.append("-e")
        if start is not None:
            args.extend(["-S", str(start)])
        if end is not None:
            args.extend(["-E", str(end)])
        return self._run(*args).stdout

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]:
        """列出窗格。默认针对 target 会话; all_sessions=True 列出全部。"""
        args = ["list-panes", "-F", _PANE_FORMAT]
        if all_sessions:
            args.append("-a")
        elif target is not None:
            args.extend(["-t", target])
        proc = self._run(*args)
        panes: list[PaneInfo] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 6:
                raise TmuxCommandError(
                    self._argv(*args),
                    0,
                    "",
                    f"无法解析 list-panes 行: {line!r}",
                )
            panes.append(
                PaneInfo(
                    session_name=parts[0],
                    window_index=int(parts[1]),
                    pane_index=int(parts[2]),
                    pane_id=parts[3],
                    pane_pid=int(parts[4]),
                    current_command=parts[5],
                )
            )
        return panes

    def kill_server(self) -> None:
        """结束本 client 对应的 tmux server。主要用于测试隔离 socket。"""
        self._run("kill-server", check=False)
