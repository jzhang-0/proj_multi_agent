"""基于 control mode 的 pane 输出流，失败时回退 pipe-pane FIFO。"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import shlex
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from tmuxctl.client import PaneInfo

CONTROL_START_TIMEOUT_SECONDS = 1.0
_END = object()


class OutputTarget(Protocol):
    """输出订阅需要的最小 tmux 接口。"""

    def command_argv(self, *args: str) -> list[str]: ...

    def list_panes(
        self,
        target: str | None = None,
        *,
        all_sessions: bool = False,
    ) -> list[PaneInfo]: ...

    def pipe_pane(self, target: str, command: str | None = None) -> None: ...


ControlFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


def decode_control_data(encoded: str) -> str:
    """解码 tmux control mode 的反斜线/三位八进制字节转义。"""
    output = bytearray()
    index = 0
    while index < len(encoded):
        char = encoded[index]
        if char != "\\":
            output.extend(char.encode("utf-8"))
            index += 1
            continue
        octal = encoded[index + 1 : index + 4]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            output.append(int(encoded[index + 1 : index + 4], 8))
            index += 4
            continue
        if index + 1 < len(encoded) and encoded[index + 1] == "\\":
            output.append(ord("\\"))
            index += 2
            continue
        output.append(ord("\\"))
        index += 1
    return output.decode("utf-8", errors="replace")


class PaneOutputStream:
    """指定 pane 的异步输出迭代器。

    正常模式保持一个 ``tmux -C`` 子进程；启动失败时切到
    ``pipe-pane`` 写 FIFO。两种模式都只根据“有无字节”提供原始文本，
    不尝试解析 TUI 的 ANSI 重绘语义。
    """

    def __init__(
        self,
        tmux: OutputTarget,
        target: str,
        *,
        startup_timeout: float = CONTROL_START_TIMEOUT_SECONDS,
        control_factory: ControlFactory = asyncio.create_subprocess_exec,
    ) -> None:
        self._tmux = tmux
        self.target = target
        self._startup_timeout = startup_timeout
        self._control_factory = control_factory
        self._pane: PaneInfo | None = None
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._control: asyncio.subprocess.Process | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._control_ready = asyncio.Event()
        self._control_error: BaseException | None = None
        self._fifo_dir: tempfile.TemporaryDirectory[str] | None = None
        self._fifo_path: Path | None = None
        self._fifo_task: asyncio.Task[None] | None = None
        self._fifo_ready = asyncio.Event()
        self._fifo_error: BaseException | None = None
        self._started = False
        self._closing = False
        self.mode: str | None = None

    def __aiter__(self) -> PaneOutputStream:
        return self

    async def __anext__(self) -> str:
        if not self._started:
            await self.start()
        item = await self._queue.get()
        if item is _END:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return str(item)

    async def __aenter__(self) -> PaneOutputStream:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def _resolve_pane(self) -> PaneInfo:
        panes = self._tmux.list_panes(self.target)
        if len(panes) != 1:
            raise ValueError(f"目标 {self.target} 应精确匹配一个 pane,实际 {len(panes)} 个")
        return panes[0]

    async def start(self) -> None:
        """启动 control mode；不可用时自动切 FIFO。"""
        if self._started:
            return
        self._pane = self._resolve_pane()
        try:
            await self._start_control()
            self.mode = "control"
        except (OSError, RuntimeError, TimeoutError):
            await self._stop_control()
            await self._start_fifo()
            self.mode = "fifo"
        self._started = True

    async def _start_control(self) -> None:
        assert self._pane is not None
        argv = self._tmux.command_argv(
            "-C",
            "attach-session",
            "-t",
            f"={self._pane.session_name}",
        )
        self._control = await self._control_factory(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._control_task = asyncio.create_task(self._read_control())
        try:
            await asyncio.wait_for(self._control_ready.wait(), timeout=self._startup_timeout)
        except TimeoutError as exc:
            raise TimeoutError("tmux control mode 启动超时") from exc
        if self._control_error is not None:
            raise RuntimeError("tmux control mode 不可用") from self._control_error

    async def _read_control(self) -> None:
        assert self._control is not None and self._control.stdout is not None
        try:
            while raw := await self._control.stdout.readline():
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("%"):
                    self._control_ready.set()
                parts = line.split(" ", 2)
                if (
                    len(parts) == 3
                    and parts[0] == "%output"
                    and self._pane is not None
                    and parts[1] == self._pane.pane_id
                ):
                    await self._queue.put(decode_control_data(parts[2]))
            if not self._control_ready.is_set():
                self._control_error = RuntimeError("control mode 未返回协议握手")
                self._control_ready.set()
            elif not self._closing:
                await self._queue.put(RuntimeError("tmux control mode 意外退出"))
        except BaseException as exc:
            self._control_error = exc
            self._control_ready.set()
            if not self._closing:
                await self._queue.put(exc)

    async def _stop_control(self) -> None:
        process = self._control
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.write(b"detach-client\n")
                with contextlib.suppress(ConnectionError):
                    await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                with contextlib.suppress(ProcessLookupError):
                    await process.wait()
        if self._control_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._control_task
        self._control = None
        self._control_task = None

    async def _start_fifo(self) -> None:
        assert self._pane is not None
        self._queue = asyncio.Queue()
        self._fifo_dir = tempfile.TemporaryDirectory(prefix="tmuxctl-output-")
        self._fifo_path = Path(self._fifo_dir.name) / "pane.fifo"
        os.mkfifo(self._fifo_path, mode=0o600)
        command = f"cat > {shlex.quote(str(self._fifo_path))}"
        try:
            self._tmux.pipe_pane(self._pane.pane_id, command)
        except Exception:
            self._fifo_dir.cleanup()
            self._fifo_dir = None
            self._fifo_path = None
            raise
        self._fifo_task = asyncio.create_task(self._read_fifo())
        try:
            await asyncio.wait_for(self._fifo_ready.wait(), timeout=1.0)
        except TimeoutError as exc:
            raise RuntimeError("pipe-pane FIFO 启动超时") from exc
        if self._fifo_error is not None:
            raise RuntimeError("pipe-pane FIFO 无法打开") from self._fifo_error

    async def _read_fifo(self) -> None:
        assert self._fifo_path is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            handle = await asyncio.to_thread(self._fifo_path.open, "rb", buffering=0)
        except BaseException as exc:
            self._fifo_error = exc
            self._fifo_ready.set()
            return
        self._fifo_ready.set()
        try:
            while chunk := await asyncio.to_thread(os.read, handle.fileno(), 4096):
                text = decoder.decode(chunk)
                if text:
                    await self._queue.put(text)
            tail = decoder.decode(b"", final=True)
            if tail:
                await self._queue.put(tail)
        finally:
            handle.close()
            if not self._closing:
                await self._queue.put(_END)

    async def close(self) -> None:
        """停止订阅并清理 control client/FIFO；不结束成员会话。"""
        if self._closing:
            return
        self._closing = True
        if self.mode == "control" or self._control is not None:
            await self._stop_control()
        if self._fifo_task is not None and self._pane is not None:
            self._tmux.pipe_pane(self._pane.pane_id, None)
            try:
                await asyncio.wait_for(self._fifo_task, timeout=1.0)
            except TimeoutError:
                self._fifo_task.cancel()
            self._fifo_task = None
        if self._fifo_dir is not None:
            self._fifo_dir.cleanup()
            self._fifo_dir = None
            self._fifo_path = None
        await self._queue.put(_END)


async def subscribe_pane(tmux: OutputTarget, target: str) -> PaneOutputStream:
    """创建并启动一个 pane 输出订阅。"""
    stream = PaneOutputStream(tmux, target)
    await stream.start()
    return stream
