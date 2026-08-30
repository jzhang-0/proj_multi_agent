"""TEAM-007：Ctrl+V 图片持久化、待发提示和两条投递路径。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image
from textual.widgets import Static

from bus import Attachment
from bus.audit import AuditLog
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.clipboard import ClipboardImageError, ClipboardImageStore
from console.compose import ComposeInput
from console.control import ControlFeedback
from console.mirror import Mirror
from tmuxctl import PaneSnapshot


def _attachment(tmp_path: Path) -> Attachment:
    target = tmp_path / "clipboard-test.png"
    Image.new("RGB", (32, 18), "navy").save(target)
    return Attachment(
        path=str(target),
        media_type="image/png",
        name=target.name,
        width=32,
        height=18,
        size=target.stat().st_size,
    )


class FakeClipboardStore:
    def __init__(self, attachment: Attachment) -> None:
        self.attachment = attachment
        self.calls = 0

    def paste(self) -> Attachment:
        self.calls += 1
        return self.attachment


async def _wait_for(pilot, predicate, rounds: int = 200) -> bool:
    for _ in range(rounds):
        if predicate():
            return True
        await pilot.pause(0.01)
    return False


def test_clipboard_store_encodes_png_under_workspace_state(tmp_path: Path) -> None:
    source = Image.new("RGBA", (80, 45), (10, 20, 30, 128))
    store = ClipboardImageStore(tmp_path / "state" / "attachments", grabber=lambda: source)

    attachment = store.paste()

    target = Path(attachment.path)
    assert target.parent == (tmp_path / "state" / "attachments").resolve()
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (attachment.width, attachment.height, attachment.size) == (80, 45, target.stat().st_size)
    assert store.paste() == attachment  # 同一图片复用内容寻址文件


def test_clipboard_store_reports_empty_clipboard(tmp_path: Path) -> None:
    store = ClipboardImageStore(tmp_path / "attachments", grabber=lambda: None)
    with pytest.raises(ClipboardImageError, match="没有图片"):
        store.paste()


def test_ctrl_v_shows_pending_image_and_at_can_send_image_only(tmp_path: Path) -> None:
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    attachment = _attachment(tmp_path)
    clipboard = FakeClipboardStore(attachment)
    sent = []
    app = ConsoleApp(
        paths,
        deliver=lambda message: sent.append(message) or True,
        members=("fable", "codex"),
        clipboard_store=clipboard,  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("ctrl+v")
            compose = app.query_one("#compose", ComposeInput)
            assert await _wait_for(pilot, lambda: compose.attachments == (attachment,))
            suggestion = app.query_one("#suggestions", Static)
            assert suggestion.display
            assert "待发图片 1 张" in str(suggestion.render())
            assert "空输入 Backspace/Delete 撤销末张" in str(suggestion.render())
            assert "Ctrl+V 添加" in str(suggestion.render())

            compose.value = "@codex"
            await pilot.press("enter")
            assert await _wait_for(pilot, lambda: len(sent) == 1)
            assert (sent[0].to, sent[0].text) == ("codex", "请查看附加图片。")
            assert sent[0].attachments == (attachment,)
            assert compose.attachments == ()
            assert not suggestion.display

    asyncio.run(scenario())
    assert clipboard.calls == 1
    audit = AuditLog(paths).entries()
    assert audit[-1]["attachments"][0]["path"] == attachment.path


def test_empty_delete_removes_pending_images_before_direct_key_passthrough(
    tmp_path: Path,
) -> None:
    class RecordingController:
        def __init__(self) -> None:
            self.keys: list[tuple[str, str]] = []

        def press_key(self, target: str, key: str) -> None:
            self.keys.append((target, key))

    first = _attachment(tmp_path)
    second_path = tmp_path / "clipboard-second.png"
    Image.new("RGB", (48, 27), "teal").save(second_path)
    second = Attachment(
        path=str(second_path),
        media_type="image/png",
        name=second_path.name,
        width=48,
        height=27,
        size=second_path.stat().st_size,
    )
    controller = RecordingController()
    app = ConsoleApp(
        BusPaths.resolve(tmp_path / "bus").ensure(),
        deliver=lambda _message: True,
        members=("codex",),
        controller=controller,  # type: ignore[arg-type]
        pump_enabled=False,
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            compose = app.query_one("#compose", ComposeInput)
            compose.attach_image(first)
            compose.attach_image(second)
            compose.focus()

            await pilot.press("backspace")
            assert compose.attachments == (first,)
            assert controller.keys == []
            assert first.path and Path(first.path).exists()
            assert second_path.exists()  # 只撤销待发引用，不删除内容寻址文件

            await pilot.press("delete")
            assert compose.attachments == ()
            assert controller.keys == []
            assert Path(first.path).exists()

            # 图片全撤完以后，直连空输入删除键才恢复终端透传。
            await pilot.press("backspace")
            assert await _wait_for(
                pilot, lambda: controller.keys == [("codex", "BSpace")]
            )

            # 本地有文字时仍只编辑文字，不撤销图片，也不透传。
            compose.attach_image(first)
            compose.value = "x"
            await pilot.press("end")
            await pilot.press("backspace")
            assert compose.value == ""
            assert compose.attachments == (first,)
            assert controller.keys == [("codex", "BSpace")]

    asyncio.run(scenario())


def test_ctrl_v_direct_member_sends_readable_path(tmp_path: Path) -> None:
    class RecordingController:
        def __init__(self) -> None:
            self.typed: list[tuple[str, str]] = []

        def type_text(self, target: str, text: str) -> ControlFeedback:
            self.typed.append((target, text))
            return ControlFeedback("type", target, True, text)

    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    attachment = _attachment(tmp_path)
    controller = RecordingController()
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        controller=controller,  # type: ignore[arg-type]
        clipboard_store=FakeClipboardStore(attachment),  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        async with app.run_test(size=(100, 26)) as pilot:
            app.select_member("codex")
            await pilot.press("ctrl+v")
            compose = app.query_one("#compose", ComposeInput)
            assert await _wait_for(pilot, lambda: bool(compose.attachments))
            await pilot.press("enter")
            assert await _wait_for(pilot, lambda: bool(controller.typed))
            target, prompt = controller.typed[0]
            assert target == "codex"
            assert "图片附件（本机路径，请逐张读取）" in prompt
            assert attachment.path in prompt

    asyncio.run(scenario())


def test_ctrl_v_in_clicked_live_input_still_uses_the_shared_image_flow(
    tmp_path: Path,
) -> None:
    class Snapshotter:
        async def capture(self, target, *, color=False, start=None):
            text = "输出\n────────────────\n❯ \n────────────────\n状态\nauto mode"
            return PaneSnapshot(target, text, color, start, None, 0.0)

    class LiveController:
        def __init__(self) -> None:
            self.calls = []

        def insert_text(self, target, text):
            self.calls.append(("text", target, text))

        def press_key(self, target, key):
            self.calls.append(("key", target, key))
            return ControlFeedback("key", target, True, key)

        def submit_live_text(self, target, text):
            self.calls.append(("submit", target, text))
            return ControlFeedback("type", target, True, text)

    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    attachment = _attachment(tmp_path)
    clipboard = FakeClipboardStore(attachment)
    controller = LiveController()
    app = ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=("codex",),
        snapshotter=Snapshotter(),
        controller=controller,  # type: ignore[arg-type]
        clipboard_store=clipboard,  # type: ignore[arg-type]
        pump_enabled=False,
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member("codex")
            mirror = app.query_one("#detail", Mirror)
            assert await _wait_for(pilot, lambda: "auto mode" in mirror.screen_text)
            await pilot.click("#detail", offset=(3, 2))
            assert mirror.live_input

            await pilot.press("ctrl+v")
            compose = app.query_one("#compose", ComposeInput)
            assert await _wait_for(pilot, lambda: compose.attachments == (attachment,))
            assert app.focused is compose
            assert not mirror.live_input
            assert controller.calls == []

    asyncio.run(scenario())
    assert clipboard.calls == 1
