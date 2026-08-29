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
            assert "Ctrl+V 继续添加" in str(suggestion.render())

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
