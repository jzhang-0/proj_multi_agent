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

    def command_argv(self, *args: str) -> list[str]:
        """为常驻子进程等高级用法构造带 socket 参数的 tmux argv。"""
        return self._argv(*args)

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

    def send_line(self, target: str, text: str) -> None:
        """一次 tmux 调用完成「字面文本 + Enter」。

        投递路径上每多一次 tmux 进程启动就多几十毫秒(机器上并发跑着几个 AI
        CLI 时更贵),所以把两条 send-keys 用 `;` 串成一次调用。
        """
        self._run(
            "send-keys", "-t", target, "-l", "--", text, ";", "send-keys", "-t", target, "Enter"
        )

    def capture_with_cursor(self, target: str, *, escape: bool = False) -> tuple[str, int]:
        """一次调用同时拿到画面和光标所在行(0 基,相对可见区顶端)。

        光标行是判断「输入框里还压着没提交的字」的关键:提交成功后输入框会
        清空,光标就不在那行字上了。`escape=True`(WEB-007 镜像帧用)相当于
        `capture_pane` 的 `-e`,保留颜色；默认 `False` 保持既有调用不变。
        """
        capture_args = ["capture-pane", "-p", "-t", target]
        if escape:
            capture_args.append("-e")
        out = self._run(
            *capture_args, ";", "display-message", "-p", "-t", target,
            "#{cursor_y}",
        ).stdout
        lines = out.splitlines()
        if not lines:
            return "", 0
        try:
            cursor_y = int(lines[-1].strip())
        except ValueError:
            return "\n".join(lines), 0
        return "\n".join(lines[:-1]), cursor_y

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

    def fit_window(self, target: str, width: int, height: int) -> None:
        """把窗口尺寸钉成指定大小。

        必须先把 `window-size` 改成 `manual`:它默认是 `latest`,会拿最近一个
        客户端的尺寸盖掉 `resize-window`(qa.visual 的 `--size` 就这么失效过)。
        两条命令用 `;` 串成一次调用,省一次 tmux 进程启动。
        """
        self._run(
            "set-option", "-t", target, "window-size", "manual",
            ";", "resize-window", "-t", target, "-x", str(width), "-y", str(height),
        )

    def release_window_size(self, target: str) -> None:
        """把窗口尺寸交还给 tmux 自己(attach 之前要还,否则贴不合客户端)。"""
        self._run("set-option", "-t", target, "-u", "window-size", check=False)

    def display_message(self, target: str, format_string: str) -> str:
        """读取一个 tmux format,不向用户界面显示。"""
        return self._run("display-message", "-p", "-t", target, format_string).stdout

    def set_pane_remain_on_exit(self, target: str, enabled: bool = True) -> None:
        """设置 pane 进程退出后是否保留窗格,供崩溃检测与 respawn 使用。"""
        value = "on" if enabled else "off"
        self._run("set-option", "-p", "-t", target, "remain-on-exit", value)

    def set_hook(self, name: str, command: str) -> None:
        """追加/设置全局 hook(调用方可用 ``name[index]`` 隔离实例)。"""
        self._run("set-hook", "-g", name, command)

    def unset_hook(self, name: str) -> None:
        """移除全局 hook；不存在时不报错。"""
        self._run("set-hook", "-gu", name, check=False)

    def show_global_option(self, name: str) -> str | None:
        """读取全局 user option；不存在或 server 已消失时返回 ``None``。"""
        process = self._run("show-options", "-gv", name, check=False)
        return process.stdout.strip() if process.returncode == 0 else None

    def unset_global_option(self, name: str) -> None:
        """移除全局 user option；不存在时不报错。"""
        self._run("set-option", "-gu", name, check=False)

    def respawn_pane(
        self,
        target: str,
        command: str | Sequence[str],
        *,
        kill: bool = True,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """在原 pane 重启命令；默认 ``-k`` 允许替换仍存活的进程。"""
        args = ["respawn-pane"]
        if kill:
            args.append("-k")
        args.extend(["-t", target])
        if cwd is not None:
            args.extend(["-c", cwd])
        if env:
            for key, value in env.items():
                args.extend(["-e", f"{key}={value}"])
        if isinstance(command, str):
            args.append(command)
        else:
            args.extend(command)
        self._run(*args)

    def pipe_pane(self, target: str, command: str | None = None) -> None:
        """把 pane 原始输出接到 shell 命令；``command=None`` 关闭现有 pipe。"""
        args = ["pipe-pane", "-t", target]
        if command is not None:
            args.append(command)
        self._run(*args)

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
