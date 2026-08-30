"""WEB-002:ConsoleApp 接上 Hub 投递租约与每成员交互租约。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

from bus import BusPaths, Message, deposit, pending
from console.app import ConsoleApp
from console.control import ControlFeedback
from console.members import MemberStatusService
from console.widgets import Timeline


class RecordingTmux:
    def __init__(self) -> None:
        self.fits: list[tuple[str, int, int]] = []
        self.releases: list[str] = []
        self.kills: list[str] = []

    def fit_window(self, member: str, width: int, height: int) -> None:
        self.fits.append((member, width, height))

    def release_window_size(self, member: str) -> None:
        self.releases.append(member)

    def kill_session(self, member: str) -> None:
        self.kills.append(member)


class RecordingController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def type_text(self, member: str, text: str) -> ControlFeedback:
        self.calls.append(("type", member, text))
        return ControlFeedback("type", member, True, text)

    def press_key(self, member: str, key: str) -> ControlFeedback:
        self.calls.append(("key", member, key))
        return ControlFeedback("key", member, True, key)

    def takeover(self, target: str) -> ControlFeedback:
        self.calls.append(("takeover", target, ""))
        return ControlFeedback("takeover", target, True, "ok")

    def record_failure(self, action: str, target: str, exc: BaseException) -> None:
        self.calls.append((f"failed-{action}", target, type(exc).__name__))


def timeline_text(app: ConsoleApp) -> str:
    timeline = app.query_one("#timeline", Timeline)
    return "\n".join("".join(segment.text for segment in line) for line in timeline.lines)


def make_app(
    paths: BusPaths,
    *,
    owner: str,
    tmux: RecordingTmux | None = None,
    controller: RecordingController | None = None,
) -> ConsoleApp:
    members = ("codex",)
    status = MemberStatusService(members, tmux)
    return ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=members,
        member_status=status,
        controller=controller,
        pump_enabled=False,
        fit_windows=True,
        lease_owner=owner,
    )


def test_two_console_pumps_only_holder_delivers_until_release(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    got_a: list[str] = []
    got_b: list[str] = []
    holder = ConsoleApp(
        paths,
        deliver=lambda message: got_a.append(message.text) or True,
        members=(),
        pump_enabled=False,
        lease_owner="tui-a",
    )
    observer = ConsoleApp(
        paths,
        deliver=lambda message: got_b.append(message.text) or True,
        members=(),
        pump_enabled=False,
        lease_owner="tui-b",
    )

    holder.pump.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not holder.hub_lease.held:
        time.sleep(0.05)
    assert holder.hub_lease.held
    observer.pump.start()
    try:
        deposit(Message.create("codex", "only-holder", sender="claude"), paths)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not got_a:
            time.sleep(0.05)
        assert got_a == ["only-holder"]
        assert got_b == []
        assert pending(paths) == []
    finally:
        holder.pump.stop()

    deposit(Message.create("codex", "after-release", sender="claude"), paths)
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not got_b:
            time.sleep(0.05)
        assert got_b == ["after-release"]
    finally:
        observer.pump.stop()


def test_observer_skips_resize_until_holder_releases(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    tmux_a = RecordingTmux()
    tmux_b = RecordingTmux()
    holder = make_app(paths, owner="tui-a", tmux=tmux_a)
    observer = make_app(paths, owner="tui-b", tmux=tmux_b)
    size_a = SimpleNamespace(width=80, height=24)
    size_b = SimpleNamespace(width=100, height=30)

    asyncio.run(holder._fit_member_window("codex", size_a))
    asyncio.run(observer._fit_member_window("codex", size_b))

    assert tmux_a.fits == [("codex", 80, 24)]
    assert tmux_b.fits == []
    assert holder.member_leases.holds("codex", "tui-a")
    assert not observer.member_leases.holds("codex", "tui-b")

    holder._release_member_interaction("codex")
    asyncio.run(observer._fit_member_window("codex", size_b))

    assert tmux_b.fits == [("codex", 100, 30)]
    assert tmux_a.kills == []
    assert tmux_b.kills == []


def test_observer_direct_keys_are_denied_and_takeover_preempts(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    tmux = RecordingTmux()
    holder_ctrl = RecordingController()
    observer_ctrl = RecordingController()
    holder = make_app(paths, owner="tui-a", tmux=tmux, controller=holder_ctrl)
    observer = make_app(paths, owner="tui-b", tmux=tmux, controller=observer_ctrl)
    assert holder._claim_member("codex")

    @contextlib.contextmanager
    def suspended():
        yield

    async def scenario() -> None:
        async with observer.run_test(size=(120, 30)) as pilot:
            observer.select_member("codex")
            observer.press_member_key("codex", "Up", "↑")
            await pilot.pause(0.05)
            assert observer_ctrl.calls == []
            assert "只观察" in timeline_text(observer)

            observer.suspend = suspended  # type: ignore[method-assign]
            observer.action_takeover_selected()
            await pilot.pause(0.05)
            assert observer.member_leases.holds("codex", "tui-b")
            assert not holder.member_leases.holds("codex", "tui-a")
            assert observer_ctrl.calls[-1][0] == "takeover"

    asyncio.run(scenario())


def test_unmount_releases_leases_and_never_kills_member_sessions(tmp_path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    tmux = RecordingTmux()
    app = make_app(paths, owner="tui-a", tmux=tmux)

    async def scenario() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            assert app._claim_member("codex")
            app.hub_lease.should_deliver()
            assert app.hub_lease.held
            await pilot.press("q")
            await pilot.pause(0.05)

    asyncio.run(scenario())
    assert tmux.kills == []
    assert app.hub_lease.holder() is None
    assert not app.hub_lease.held
    assert app.member_leases.holder("codex") is None
    assert not app.pump.is_running()
