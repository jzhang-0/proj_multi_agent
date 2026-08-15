"""TMX-003:control mode 输出订阅与 pipe-pane FIFO 回退。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import PaneOutputStream, Tmux, decode_control_data


def test_control_mode_escape_decoder_handles_bytes_and_utf8() -> None:
    assert decode_control_data(r"hello\040world\015\012") == "hello world\r\n"
    assert decode_control_data(r"path\\name") == r"path\name"
    assert decode_control_data(r"\344\275\240\345\245\275") == "你好"


async def receive_until(stream: PaneOutputStream, needle: str, timeout: float = 2.0) -> str:
    collected = ""
    deadline = time.monotonic() + timeout
    while needle not in collected:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"未在输出中看到 {needle!r}: {collected!r}")
        collected += await asyncio.wait_for(stream.__anext__(), timeout=remaining)
    return collected


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    client = Tmux(socket_name=f"tmx003-{uuid.uuid4().hex[:8]}", timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_control_mode_streams_only_target_pane(isolated_tmux: Tmux) -> None:
    async def scenario():
        session = f"control-{uuid.uuid4().hex[:6]}"
        isolated_tmux.new_session(session, command="cat")
        pane_id = isolated_tmux.list_panes(session)[0].pane_id
        try:
            async with PaneOutputStream(isolated_tmux, pane_id) as stream:
                assert stream.mode == "control"
                isolated_tmux.send_keys(pane_id, "CONTROL_OK", literal=True)
                isolated_tmux.send_keys(pane_id, "Enter")
                assert "CONTROL_OK" in await receive_until(stream, "CONTROL_OK")
        finally:
            isolated_tmux.kill_session(session, missing_ok=True)

    asyncio.run(scenario())


def test_real_fifo_fallback_streams_output_and_cleans_pipe(isolated_tmux: Tmux) -> None:
    async def unavailable(*_args, **_kwargs):
        raise OSError("control mode unavailable")

    async def scenario():
        session = f"fifo-{uuid.uuid4().hex[:6]}"
        isolated_tmux.new_session(session, command="cat")
        pane_id = isolated_tmux.list_panes(session)[0].pane_id
        try:
            stream = PaneOutputStream(isolated_tmux, pane_id, control_factory=unavailable)
            async with stream:
                assert stream.mode == "fifo"
                isolated_tmux.send_keys(pane_id, "FIFO_OK", literal=True)
                isolated_tmux.send_keys(pane_id, "Enter")
                assert "FIFO_OK" in await receive_until(stream, "FIFO_OK")
            assert isolated_tmux.has_session(session)
        finally:
            isolated_tmux.kill_session(session, missing_ok=True)

    asyncio.run(scenario())
