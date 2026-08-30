"""从系统剪贴板读取图片，并保存为工作区内可审计的附件。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from bus import Attachment
from control.attachments import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    ContentAddressedImageStore,
    ImageAttachmentError,
)


class ClipboardImageError(RuntimeError):
    """剪贴板没有可用图片，或图片无法安全持久化。"""


def _grab_clipboard() -> Any:
    from PIL import ImageGrab

    return ImageGrab.grabclipboard()


class ClipboardImageStore:
    """把 `Ctrl+V` 取得的图片规范化成 PNG，并返回消息附件元数据。"""

    def __init__(
        self,
        root: str | Path,
        *,
        grabber: Callable[[], Any] | None = None,
        max_pixels: int = MAX_IMAGE_PIXELS,
        max_bytes: int = MAX_IMAGE_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.grabber = grabber or _grab_clipboard
        self.max_pixels = max_pixels
        self.max_bytes = max_bytes
        self.store = ContentAddressedImageStore(
            self.root,
            max_pixels=max_pixels,
            max_bytes=max_bytes,
        )

    def paste(self) -> Attachment:
        try:
            image = self.grabber()
        except Exception as exc:
            raise ClipboardImageError(f"读取系统剪贴板失败: {exc}") from exc
        if image is None or isinstance(image, list):
            raise ClipboardImageError("剪贴板里没有图片")
        try:
            return self.store.store_image(image)
        except ImageAttachmentError as exc:
            raise ClipboardImageError(f"剪贴板{exc}") from exc


def attachment_prompt(attachments: tuple[Attachment, ...]) -> str:
    """把附件变成目标 CLI 能直接读取的一行本机路径提示。"""
    if not attachments:
        return ""
    paths = "；".join(item.path for item in attachments)
    return f"图片附件（本机路径，请逐张读取）: {paths}"
