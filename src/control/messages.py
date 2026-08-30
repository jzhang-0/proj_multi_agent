"""Web/TUI 可共用的消息、ask/reply 与附件编排。"""

from __future__ import annotations

from dataclasses import dataclass

from bus import (
    AskError,
    Attachment,
    BusPaths,
    Message,
    deposit,
    load_ask,
    store_ask,
    store_reply,
)
from control.attachments import ContentAddressedImageStore
from roster.schema import RosterError, validate_member_name
from work import WorkService
from work.model import validate_task_id


class ComposeError(ValueError):
    """消息组合参数不符合 TUI/总线既有语义。"""


class TargetNotFound(ComposeError):
    """收件人语法有效，但不在当前工作区名册。"""


@dataclass(frozen=True)
class MessageReceipt:
    id: str
    to: str
    kind: str
    reply_to: str | None
    task_id: str | None
    attachment_ids: tuple[str, ...]


class MessageComposeService:
    """在 UI/HTTP 之外执行目标、任务、ask 与附件校验后原子入队。"""

    def __init__(
        self,
        paths: BusPaths,
        *,
        members: tuple[str, ...],
        leader: str,
        attachments: ContentAddressedImageStore,
        work: WorkService | None = None,
    ) -> None:
        self.paths = paths
        self.members = frozenset(members)
        self.leader = leader
        self.attachments = attachments
        self.work = work

    def send(
        self,
        *,
        actor: str,
        text: str,
        to: str | None = None,
        kind: str = "message",
        task_id: str | None = None,
        reply_to: str | None = None,
        attachment_ids: tuple[str, ...] = (),
    ) -> MessageReceipt:
        if not isinstance(actor, str) or not actor:
            raise ComposeError("认证 actor 不可用")
        if not isinstance(text, str):
            raise ComposeError("text 必须是字符串")
        if len(attachment_ids) > 8:
            raise ComposeError("单条消息最多携带 8 张图片")
        attachments = tuple(self.attachments.resolve(item) for item in attachment_ids)
        content = text if text.strip() else ("请查看附加图片。" if attachments else "")
        if not content:
            raise ComposeError("消息正文与图片不能同时为空")

        if reply_to is not None:
            if kind != "reply":
                raise ComposeError("reply_to 只能与 kind=reply 一起使用")
            if to is not None:
                raise ComposeError("reply 的收件人由原 ask 决定，不能自报 to")
            if task_id is not None:
                raise ComposeError("task 暂不与 reply 一起使用")
            message = self._reply(actor, reply_to, content, attachments)
        elif kind == "ask":
            if task_id is not None:
                raise ComposeError("task 暂不与 ask 一起使用")
            message = Message.create(
                self._target(to),
                content,
                sender=actor,
                kind="ask",
                attachments=attachments,
            )
            state = store_ask(message, self.paths)
            try:
                deposit(message, self.paths)
            except Exception:
                state.unlink(missing_ok=True)
                raise
        elif kind == "message":
            linked_task = self._task(task_id)
            message = Message.create(
                self._target(to),
                content,
                sender=actor,
                task=linked_task,
                attachments=attachments,
            )
            deposit(message, self.paths)
        else:
            raise ComposeError("kind 必须是 message、ask 或 reply")

        return MessageReceipt(
            id=message.id or "",
            to=message.to,
            kind=message.kind or "message",
            reply_to=message.reply_to,
            task_id=message.task,
            attachment_ids=attachment_ids,
        )

    def _target(self, requested: str | None) -> str:
        target = requested or self.leader
        try:
            validate_member_name(target)
        except RosterError as exc:
            raise ComposeError(str(exc)) from exc
        if target not in self.members:
            raise TargetNotFound(f"成员 {target} 不在当前工作区名册")
        return target

    def _task(self, task_id: str | None) -> str | None:
        if task_id is None:
            return None
        validate_task_id(task_id)
        if self.work is None:
            raise ComposeError("当前工作区没有绑定任务账本")
        self.work.snapshot().get(task_id)
        return task_id

    def _reply(
        self,
        actor: str,
        ask_id: str,
        text: str,
        attachments: tuple[Attachment, ...],
    ) -> Message:
        ask = load_ask(ask_id, self.paths)
        if ask is None:
            raise AskError(f"找不到 ask {ask_id}")
        message = Message.create(
            ask.sender,
            text,
            sender=actor,
            kind="reply",
            reply_to=ask_id,
            attachments=attachments,
        )
        state = store_reply(message, self.paths)
        try:
            deposit(message, self.paths)
        except Exception:
            state.unlink(missing_ok=True)
            raise
        return message
