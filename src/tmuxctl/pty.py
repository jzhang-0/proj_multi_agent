"""固定 tmux attach PTY 与 window-size 崩溃恢复看门进程。"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Mapping
from typing import Protocol


class AttachTmux(Protocol):
    def attach_argv(self, target: str) -> list[str]: ...
    def release_window_size_argv(self, target: str) -> list[str]: ...


def _child_env(source: Mapping[str, str]) -> dict[str, str]:
    """只传终端启动所需变量；Web cookie/token 和 amux 内部变量不会继承。"""
    env = {"TERM": "xterm-256color"}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMUX_TMPDIR"):
        value = source.get(key)
        if value:
            env[key] = value
    return env


class TmuxAttachProcess:
    """一个无 shell、argv 不可由浏览器指定的 ``tmux attach-session`` 客户端。"""

    def __init__(self, pid: int, master_fd: int) -> None:
        self.pid = pid
        self.master_fd = master_fd
        self.returncode: int | None = None

    @classmethod
    def spawn(
        cls,
        tmux: AttachTmux,
        target: str,
        *,
        cols: int,
        rows: int,
        environ: Mapping[str, str] = os.environ,
    ) -> TmuxAttachProcess:
        argv = tmux.attach_argv(target)
        master_fd, slave_fd = pty.openpty()
        _resize(slave_fd, cols, rows)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - exec replaces this branch
            try:
                os.close(master_fd)
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                for target_fd in (0, 1, 2):
                    os.dup2(slave_fd, target_fd)
                if slave_fd > 2:
                    os.close(slave_fd)
                os.execvpe(argv[0], argv, _child_env(environ))
            except BaseException:
                os._exit(127)
        os.close(slave_fd)
        os.set_blocking(master_fd, False)
        return cls(pid, master_fd)

    def resize(self, cols: int, rows: int) -> None:
        _resize(self.master_fd, cols, rows)

    def read(self, size: int = 65536) -> bytes | None:
        """``None`` 表示暂时无数据，``b''`` 表示 PTY 已结束。"""
        try:
            return os.read(self.master_fd, size)
        except BlockingIOError:
            return None
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.master_fd, view)
            except BlockingIOError:
                select.select([], [self.master_fd], [], 0.1)
                continue
            view = view[written:]

    def close(self, timeout: float = 2.0) -> int:
        """关主端令 attach 收到 HUP，并始终 waitpid 回收子进程。"""
        if self.returncode is not None:
            return self.returncode
        with contextlib.suppress(OSError):
            os.close(self.master_fd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waited, status = os.waitpid(self.pid, os.WNOHANG)
            if waited == self.pid:
                self.returncode = os.waitstatus_to_exitcode(status)
                return self.returncode
            time.sleep(0.02)
        with contextlib.suppress(ProcessLookupError):
            os.kill(self.pid, signal.SIGHUP)
        waited, status = os.waitpid(self.pid, 0)
        self.returncode = os.waitstatus_to_exitcode(status) if waited else -signal.SIGHUP
        return self.returncode


def _resize(fd: int, cols: int, rows: int) -> None:
    if cols < 1 or rows < 1:
        raise ValueError("PTY 尺寸必须为正数")
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class WindowSizeGuard:
    """父进程即使 SIGKILL，独立看门进程也会恢复所有 manual window-size。

    父子只用 pipe 传 tmuxctl 生成的 argv 数组；子进程从不解释 shell 字符串。
    写端随 Web 进程消失而 EOF，因此不依赖 ``finally`` 或 PID 轮询。
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._fd: int | None = None
        self._tracked: set[str] = set()
        self._closed = False

    def _ensure_started(self) -> None:
        if self._process is not None:
            return
        read_fd, write_fd = os.pipe()
        # uvicorn 的 lifespan 起了线程与 asyncio 子进程后再 ``fork``，子进程
        # 继续执行 Python/``subprocess.run`` 在 macOS 上不可靠；实测 Web 父
        # 进程 SIGKILL 后旧实现的 guard 会退出却不执行恢复。另外 fork 会把
        # uvicorn 的监听 socket 一并继承。这里 exec 一个最小 helper，只传读
        # 管道；``close_fds`` 保证 Web 的 cookie socket/PTY/监听 fd 都不过去。
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from tmuxctl.pty import WindowSizeGuard; "
                    "WindowSizeGuard._guard_main(int(sys.argv[1]))"
                ),
                str(read_fd),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(read_fd,),
            start_new_session=True,
        )
        os.close(read_fd)
        self._process = process
        self._fd = write_fd

    def track(self, tmux: AttachTmux, target: str, *, identity: str = "") -> None:
        key = identity or target
        if key in self._tracked or self._closed:
            return
        self._ensure_started()
        self._send({"op": "track", "key": key, "argv": tmux.release_window_size_argv(target)})
        self._tracked.add(key)

    def untrack(self, target: str, *, identity: str = "") -> None:
        key = identity or target
        if key not in self._tracked or self._closed:
            return
        self._send({"op": "untrack", "key": key})
        self._tracked.discard(key)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._fd is None or self._process is None:
            return
        os.close(self._fd)
        with contextlib.suppress(ChildProcessError):
            self._process.wait()

    def _send(self, payload: dict[str, object]) -> None:
        raw = (json.dumps(payload, ensure_ascii=True) + "\n").encode("ascii")
        assert self._fd is not None
        os.write(self._fd, raw)

    @staticmethod
    def _guard_main(read_fd: int) -> None:
        tracked: dict[str, list[str]] = {}
        with os.fdopen(read_fd, "rb", buffering=0) as pipe:
            pending = b""
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    try:
                        item = json.loads(line)
                        key = str(item["key"])
                        if item["op"] == "track":
                            argv = item["argv"]
                            if isinstance(argv, list) and all(isinstance(v, str) for v in argv):
                                tracked[key] = argv
                        elif item["op"] == "untrack":
                            tracked.pop(key, None)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
        for argv in tracked.values():
            subprocess.run(argv, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
