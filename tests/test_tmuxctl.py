"""TMX-001: tmux 版本探测与命令封装。"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator

import pytest

from tmuxctl import (
    MIN_VERSION,
    Tmux,
    TmuxCommandError,
    TmuxNotFoundError,
    TmuxTimeoutError,
    TmuxVersionError,
    probe,
)
from tmuxctl.version import parse_version


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_parse_version_accepts_patch_suffix() -> None:
    assert parse_version("tmux 3.2").as_tuple() == (3, 2)
    parsed = parse_version("tmux 3.3a")
    assert parsed.major == 3
    assert parsed.minor == 3
    assert parsed.suffix == "a"
    assert str(parse_version("tmux 3.5a")) == "3.5a"


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(TmuxVersionError, match="无法解析"):
        parse_version("not a version")


def test_probe_rejects_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(TmuxNotFoundError, match="未找到 tmux"):
        probe("tmux")


def test_probe_rejects_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="tmux 3.1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(TmuxVersionError, match="版本过低"):
        probe("tmux")
    assert MIN_VERSION == (3, 2)


def test_probe_accepts_minimum_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="tmux 3.2\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    version = probe("tmux")
    assert version.as_tuple() >= MIN_VERSION


def test_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raw = kwargs.get("timeout", 5.0)
        limit = float(raw) if isinstance(raw, (int, float)) else 5.0
        raise subprocess.TimeoutExpired(args, limit)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(TmuxTimeoutError, match="超时"):
        probe("tmux", timeout=0.1)


def test_has_session_uses_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[-2:] == ["-V"] or "-V" in args:
            return _completed(args, stdout="tmux 3.5a\n")
        if "has-session" in args:
            return _completed(args, returncode=1)
        return _completed(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = Tmux()
    assert client.has_session("claude") is False
    has_call = next(c for c in calls if "has-session" in c)
    assert has_call[-2:] == ["-t", "=claude"]


def test_send_keys_does_not_use_equals_target(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "-V" in args:
            return _completed(args, stdout="tmux 3.5a\n")
        return _completed(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = Tmux()
    client.send_keys("claude", "hello", literal=True)
    send = next(c for c in calls if "send-keys" in c)
    assert "-t" in send
    target = send[send.index("-t") + 1]
    assert target == "claude"
    assert "=claude" not in send
    assert "-l" in send
    assert send[-1] == "hello"


def test_new_session_and_list_panes_and_capture_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "-V" in args:
            return _completed(args, stdout="tmux 3.5a\n")
        if "list-panes" in args:
            return _completed(args, stdout="s1\t0\t0\t%0\t1234\tcat\n")
        if "capture-pane" in args:
            return _completed(args, stdout="hello\n")
        return _completed(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = Tmux(socket_name="testsock")
    client.new_session("s1", command=["cat"], cwd="/tmp", env={"AGENT_NAME": "s1"})
    panes = client.list_panes("s1")
    text = client.capture_pane("s1", escape=True, start="-")
    client.kill_session("s1")

    new = next(c for c in calls if "new-session" in c)
    assert "-L" in new and "testsock" in new
    assert new[new.index("-s") + 1] == "s1"
    assert "-d" in new
    assert "-c" in new and "/tmp" in new
    assert "-e" in new and "AGENT_NAME=s1" in new
    assert new[-1] == "cat"

    assert len(panes) == 1
    assert panes[0].pane_id == "%0"
    assert panes[0].pane_pid == 1234
    assert text == "hello\n"

    capture = next(c for c in calls if "capture-pane" in c)
    assert "-p" in capture and "-e" in capture and "-S" in capture

    kill = next(c for c in calls if "kill-session" in c)
    assert kill[-2:] == ["-t", "=s1"]


def test_command_error_includes_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-V" in args:
            return _completed(args, stdout="tmux 3.5a\n")
        return _completed(args, returncode=1, stderr="duplicate session: s1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = Tmux()
    with pytest.raises(TmuxCommandError, match="duplicate session") as exc:
        client.new_session("s1")
    assert exc.value.returncode == 1


def test_timeout_on_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-V" in args:
            return _completed(args, stdout="tmux 3.5a\n")
        raw = kwargs.get("timeout", 5.0)
        limit = float(raw) if isinstance(raw, (int, float)) else 5.0
        raise subprocess.TimeoutExpired(args, limit)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = Tmux(timeout=0.2)
    with pytest.raises(TmuxTimeoutError, match="超时"):
        client.has_session("x")


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    socket = f"tmx001-{uuid.uuid4().hex[:8]}"
    client = Tmux(socket_name=socket, timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_real_tmux_session_lifecycle(isolated_tmux: Tmux) -> None:
    name = f"sess-{uuid.uuid4().hex[:6]}"
    prefix = name[:-1]
    isolated_tmux.new_session(name, command=["sleep", "30"])
    try:
        assert isolated_tmux.has_session(name) is True
        assert isolated_tmux.has_session(prefix) is False
        panes = isolated_tmux.list_panes(name)
        assert len(panes) == 1
        assert panes[0].session_name == name
        assert panes[0].pane_pid > 0
        snapshot = isolated_tmux.capture_pane(name)
        assert isinstance(snapshot, str)
        isolated_tmux.send_keys(name, "x", literal=True)
    finally:
        isolated_tmux.kill_session(name, missing_ok=True)
    assert isolated_tmux.has_session(name) is False
