"""UI 无关的内容寻址图片附件存储。"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from bus import Attachment

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{16}\Z")
_NAME = re.compile(r"clipboard-([0-9a-f]{16})\.png\Z")


class ImageAttachmentError(ValueError):
    """上传内容不是可接受的图片，或附件 id 不合法。"""


def attachment_id_from_name(name: str) -> str | None:
    """从既有内容寻址文件名提取可安全出网的 id。"""
    matched = _NAME.fullmatch(Path(name).name)
    return matched.group(1) if matched is not None else None


class ContentAddressedImageStore:
    """把图片规范化成 PNG，按内容哈希保存并以不透明 id 取回。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_pixels: int = MAX_IMAGE_PIXELS,
        max_bytes: int = MAX_IMAGE_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_pixels = max_pixels
        self.max_bytes = max_bytes

    def store_upload(self, payload: bytes) -> Attachment:
        if not payload:
            raise ImageAttachmentError("图片内容为空")
        if len(payload) > self.max_bytes:
            raise ImageAttachmentError(
                f"上传图片为 {len(payload)} 字节，上限 {self.max_bytes} 字节"
            )
        try:
            with Image.open(BytesIO(payload)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > self.max_pixels:
                    raise ImageAttachmentError("上传图片尺寸无效或超过像素上限")
                opened.load()
                return self.store_image(opened)
        except ImageAttachmentError:
            raise
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
            raise ImageAttachmentError("上传内容不是可读取的图片") from exc

    def store_image(self, image: Any) -> Attachment:
        width = getattr(image, "width", 0)
        height = getattr(image, "height", 0)
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise ImageAttachmentError("图片尺寸无效")
        if width * height > self.max_pixels:
            raise ImageAttachmentError(f"图片过大: {width}×{height}，上限 {self.max_pixels} 像素")

        normalized = image
        mode = getattr(image, "mode", "")
        if mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
            bands = tuple(getattr(image, "getbands", lambda: ())())
            normalized = image.convert("RGBA" if "A" in bands else "RGB")
        encoded = BytesIO()
        try:
            normalized.save(encoded, format="PNG", optimize=True)
        except Exception as exc:
            raise ImageAttachmentError(f"图片无法编码为 PNG: {exc}") from exc
        payload = encoded.getvalue()
        if not payload:
            raise ImageAttachmentError("图片编码结果为空")
        if len(payload) > self.max_bytes:
            raise ImageAttachmentError(
                f"图片编码后为 {len(payload)} 字节，上限 {self.max_bytes} 字节"
            )

        digest = hashlib.sha256(payload).hexdigest()[:16]
        name = f"clipboard-{digest}.png"
        target = self.root / name
        self.root.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f".{name}.", dir=self.root, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                assert temporary is not None
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return self._attachment(target, width=width, height=height, size=len(payload))

    def resolve(self, attachment_id: str) -> Attachment:
        if not isinstance(attachment_id, str) or _ATTACHMENT_ID.fullmatch(attachment_id) is None:
            raise ImageAttachmentError("附件 id 必须是 16 位小写十六进制")
        target = self.root / f"clipboard-{attachment_id}.png"
        if target.is_symlink() or not target.is_file():
            raise FileNotFoundError(attachment_id)
        payload = target.read_bytes()
        size = len(payload)
        if size <= 0 or size > self.max_bytes:
            raise ImageAttachmentError("附件文件大小无效")
        if hashlib.sha256(payload).hexdigest()[:16] != attachment_id:
            raise ImageAttachmentError("附件内容与 id 不匹配")
        try:
            with Image.open(BytesIO(payload)) as image:
                width, height = image.size
                image.verify()
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
            raise ImageAttachmentError("附件文件不是有效图片") from exc
        if width <= 0 or height <= 0 or width * height > self.max_pixels:
            raise ImageAttachmentError("附件图片尺寸无效")
        return self._attachment(target, width=width, height=height, size=size)

    def read(self, attachment_id: str) -> tuple[Attachment, bytes]:
        attachment = self.resolve(attachment_id)
        return attachment, Path(attachment.path).read_bytes()

    @staticmethod
    def identifier(attachment: Attachment) -> str:
        attachment_id = attachment_id_from_name(attachment.name)
        if attachment_id is None:
            raise ImageAttachmentError("附件不是内容寻址图片")
        return attachment_id

    @staticmethod
    def _attachment(target: Path, *, width: int, height: int, size: int) -> Attachment:
        return Attachment(
            path=str(target),
            media_type="image/png",
            name=target.name,
            width=width,
            height=height,
            size=size,
        )
