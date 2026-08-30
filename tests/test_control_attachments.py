"""WEB-009 收口项(c):附件存储容量上限，内容寻址去重不占二次配额。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from control.attachments import ContentAddressedImageStore, ImageAttachmentError


def _png(color: str = "navy") -> bytes:
    payload = BytesIO()
    Image.new("RGB", (4, 4), color).save(payload, format="PNG")
    return payload.getvalue()


def _stored_size(tmp_path: Path, color: str) -> int:
    """图片会被 `store_image()` 重新编码为 PNG，实际落盘字节数和输入不一定
    相等(`optimize=True`)；先探一次真实落盘大小再定上限，别拿输入字节数猜。
    """
    probe = ContentAddressedImageStore(tmp_path / "probe")
    return probe.store_upload(_png(color=color)).size


def test_store_rejects_new_upload_once_total_size_cap_reached(tmp_path: Path) -> None:
    cap = _stored_size(tmp_path, "navy")
    store = ContentAddressedImageStore(tmp_path / "attachments", max_store_bytes=cap)
    store.store_upload(_png(color="navy"))  # 刚好写满上限

    with pytest.raises(ImageAttachmentError, match="容量上限"):
        store.store_upload(_png(color="crimson"))


def test_store_reuse_of_existing_content_does_not_count_against_cap(tmp_path: Path) -> None:
    payload = _png(color="navy")
    cap = _stored_size(tmp_path, "navy")
    store = ContentAddressedImageStore(tmp_path / "attachments", max_store_bytes=cap)
    first = store.store_upload(payload)
    second = store.store_upload(payload)  # 同一张图,内容寻址去重,不应算二次占用

    assert first.name == second.name
