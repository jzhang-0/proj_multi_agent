"""WEB-006 写端点的解析与控制面编排；不信任浏览器自报身份或路径。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from bus import AskError
from control.attachments import (
    MAX_IMAGE_BYTES,
    ContentAddressedImageStore,
    ImageAttachmentError,
)
from control.messages import ComposeError, MessageComposeService, TargetNotFound
from team.model import TeamValidationError
from team.store import TeamNotFound
from web.context import (
    SnapshotContext,
    load_bound_team,
    require_paths,
    require_workspace,
)
from web.errors import ApiError
from work import LedgerCorruptionError, WorkError, WorkService, WorkValidationError
from work.model import validate_task_id
from workspace.errors import WorkspaceError

_MESSAGE_FIELDS = frozenset(
    {"to", "text", "kind", "task_id", "reply_to", "attachment_ids"}
)


async def read_message_payload(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise ApiError(
            "invalid-request", "请求体必须是 JSON 对象", status_code=400, domain="message"
        ) from exc
    if not isinstance(raw, dict):
        raise ApiError(
            "invalid-request", "请求体必须是 JSON 对象", status_code=400, domain="message"
        )
    unknown = sorted(set(raw) - _MESSAGE_FIELDS)
    if unknown:
        raise ApiError(
            "invalid-request",
            f"消息请求含未知或不可信字段: {', '.join(unknown)}",
            status_code=400,
            domain="message",
        )
    payload: dict[str, Any] = {}
    for key in ("to", "task_id", "reply_to"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ApiError(
                "invalid-request", f"{key} 必须是非空字符串", status_code=400, domain="message"
            )
        payload[key] = value
    text = raw.get("text", "")
    kind = raw.get("kind", "message")
    attachment_ids = raw.get("attachment_ids", [])
    if not isinstance(text, str):
        raise ApiError(
            "invalid-request", "text 必须是字符串", status_code=400, domain="message"
        )
    if not isinstance(kind, str):
        raise ApiError(
            "invalid-request", "kind 必须是字符串", status_code=400, domain="message"
        )
    if not isinstance(attachment_ids, list) or not all(
        isinstance(item, str) for item in attachment_ids
    ):
        raise ApiError(
            "invalid-request",
            "attachment_ids 必须是字符串数组",
            status_code=400,
            domain="message",
        )
    if payload["task_id"] is not None:
        try:
            validate_task_id(payload["task_id"])
        except WorkValidationError as exc:
            raise ApiError(
                "invalid-request", str(exc), status_code=400, domain="message"
            ) from exc
    payload.update(text=text, kind=kind, attachment_ids=tuple(attachment_ids))
    return payload


async def read_image_body(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/"):
        raise ApiError(
            "invalid-request",
            "附件上传 Content-Type 必须是 image/*",
            status_code=400,
            domain="attachment",
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ApiError(
                "invalid-request",
                "Content-Length 无效",
                status_code=400,
                domain="attachment",
            ) from exc
        if declared_size > MAX_IMAGE_BYTES:
            raise ApiError(
                "invalid-request",
                f"上传图片超过 {MAX_IMAGE_BYTES} 字节上限",
                status_code=400,
                domain="attachment",
            )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ApiError(
                "invalid-request",
                f"上传图片超过 {MAX_IMAGE_BYTES} 字节上限",
                status_code=400,
                domain="attachment",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def attachment_store(ctx: SnapshotContext) -> ContentAddressedImageStore:
    workspace = require_workspace(ctx)
    return ContentAddressedImageStore(workspace.state_dir / "attachments")


def upload_attachment(ctx: SnapshotContext, payload: bytes) -> dict[str, Any]:
    store = attachment_store(ctx)
    try:
        attachment = store.store_upload(payload)
    except ImageAttachmentError as exc:
        raise ApiError(
            "invalid-request", str(exc), status_code=400, domain="attachment"
        ) from exc
    attachment_id = store.identifier(attachment)
    return {
        "attachment": {
            "id": attachment_id,
            "name": attachment.name,
            "media_type": attachment.media_type,
            "width": attachment.width,
            "height": attachment.height,
            "size": attachment.size,
            "download_url": f"/api/v1/attachments/{attachment_id}",
        }
    }


def download_attachment(ctx: SnapshotContext, attachment_id: str) -> Response:
    try:
        attachment, payload = attachment_store(ctx).read(attachment_id)
    except FileNotFoundError as exc:
        raise ApiError(
            "not-found", "附件不存在", status_code=404, domain="attachment"
        ) from exc
    except ImageAttachmentError as exc:
        raise ApiError(
            "invalid-request", str(exc), status_code=400, domain="attachment"
        ) from exc
    return Response(
        content=payload,
        media_type=attachment.media_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.name}"'},
    )


def send_message(ctx: SnapshotContext, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    workspace = require_workspace(ctx)
    paths = require_paths(ctx)
    try:
        _binding, team = load_bound_team(workspace)
        if team is None:
            raise ApiError(
                "team-unbound",
                "当前工作区尚未绑定团队",
                status_code=409,
                domain="message",
            )
        service = MessageComposeService(
            paths,
            members=ctx.names,
            leader=team.leader,
            attachments=attachment_store(ctx),
            work=WorkService.for_workspace(workspace),
        )
        receipt = service.send(actor=actor, **payload)
    except ApiError:
        raise
    except TargetNotFound as exc:
        raise ApiError("not-found", str(exc), status_code=404, domain="message") from exc
    except FileNotFoundError as exc:
        raise ApiError(
            "not-found", "附件不存在", status_code=404, domain="attachment"
        ) from exc
    except (ComposeError, AskError, ImageAttachmentError) as exc:
        raise ApiError(
            "invalid-request", str(exc), status_code=400, domain="message"
        ) from exc
    except WorkValidationError as exc:
        raise ApiError("not-found", str(exc), status_code=404, domain="work") from exc
    except (LedgerCorruptionError, WorkError, OSError) as exc:
        raise ApiError(
            "work-unavailable", str(exc), status_code=503, domain="message"
        ) from exc
    except (TeamNotFound, TeamValidationError, WorkspaceError) as exc:
        raise ApiError(
            "work-unavailable", str(exc), status_code=503, domain="message"
        ) from exc
    return {"ok": True, "message": asdict(receipt)}
