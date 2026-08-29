"""从系统剪贴板读取图片，并保存为工作区内可审计的附件。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

from bus import Attachment

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 20 * 1024 * 1024


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

    def paste(self) -> Attachment:
        try:
            image = self.grabber()
        except Exception as exc:
            raise ClipboardImageError(f"读取系统剪贴板失败: {exc}") from exc
        if image is None or isinstance(image, list):
            raise ClipboardImageError("剪贴板里没有图片")
        width = getattr(image, "width", 0)
        height = getattr(image, "height", 0)
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise ClipboardImageError("剪贴板图片尺寸无效")
        if width * height > self.max_pixels:
            raise ClipboardImageError(
                f"剪贴板图片过大: {width}×{height}，上限 {self.max_pixels} 像素"
            )

        payload = BytesIO()
        try:
            image.save(payload, format="PNG", optimize=True)
        except Exception as exc:
            raise ClipboardImageError(f"剪贴板图片无法编码为 PNG: {exc}") from exc
        encoded = payload.getvalue()
        if not encoded:
            raise ClipboardImageError("剪贴板图片编码结果为空")
        if len(encoded) > self.max_bytes:
            raise ClipboardImageError(
                f"剪贴板图片编码后为 {len(encoded)} 字节，上限 {self.max_bytes} 字节"
            )

        digest = hashlib.sha256(encoded).hexdigest()[:16]
        name = f"clipboard-{digest}.png"
        target = self.root / name
        self.root.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{name}.",
                    dir=self.root,
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                assert temporary is not None
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        return Attachment(
            path=str(target),
            media_type="image/png",
            name=name,
            width=width,
            height=height,
            size=len(encoded),
        )


def attachment_prompt(attachments: tuple[Attachment, ...]) -> str:
    """把附件变成目标 CLI 能直接读取的一行本机路径提示。"""
    if not attachments:
        return ""
    paths = "；".join(item.path for item in attachments)
    return f"图片附件（本机路径，请逐张读取）: {paths}"
