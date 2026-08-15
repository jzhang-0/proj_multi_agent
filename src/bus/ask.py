"""ask/reply 的关联索引与等待语义。"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from bus.message import MalformedMessage, Message
from bus.paths import BusPaths
from bus.queue import read_message

DEFAULT_ASK_TIMEOUT_SECONDS = 10 * 60.0
ASK_POLL_SECONDS = 0.1

_MESSAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class AskError(ValueError):
    """ask/reply 参数、身份或关联状态不合法。"""


def validate_ask_id(ask_id: str) -> str:
    """校验可安全用作索引文件名的 ask id。"""
    if not _MESSAGE_ID.fullmatch(ask_id):
        raise AskError("ask id 只能含字母、数字、下划线和连字符,长度 1～128")
    return ask_id


def _write_once(path: Path, message: Message) -> Path:
    """原子写一条关联记录,同一 id 只接受第一次。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(message.to_dict(), ensure_ascii=False), encoding="utf-8")
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise AskError(f"ask {path.stem} 已有记录") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _load(path: Path) -> Message | None:
    if not path.is_file():
        return None
    try:
        return read_message(path)
    except MalformedMessage as exc:
        raise AskError(f"关联记录损坏:{path.name}: {exc}") from exc


def store_ask(message: Message, paths: BusPaths) -> Path:
    """保存提问索引,让 ``--reply <id>`` 能找到原发件人。"""
    if message.kind != "ask" or message.id is None:
        raise AskError("提问必须带 kind=ask 和 id")
    ask_id = validate_ask_id(message.id)
    return _write_once(paths.asks / f"{ask_id}.json", message)


def load_ask(ask_id: str, paths: BusPaths) -> Message | None:
    """按 id 读取原提问。"""
    return _load(paths.asks / f"{validate_ask_id(ask_id)}.json")


def store_reply(message: Message, paths: BusPaths) -> Path:
    """保存首个回复,并验证它确实关联且来自原收件人。"""
    if message.kind != "reply" or message.reply_to is None:
        raise AskError("回复必须带 kind=reply 和 replyTo")
    ask_id = validate_ask_id(message.reply_to)
    ask = load_ask(ask_id, paths)
    if ask is None:
        raise AskError(f"找不到 ask {ask_id}")
    if message.sender != ask.to or message.to != ask.sender:
        raise AskError(f"只有提问收件人 {ask.to} 可以回复 ask {ask_id}")
    return _write_once(paths.replies / f"{ask_id}.json", message)


def load_reply(ask_id: str, paths: BusPaths) -> Message | None:
    """读取 ask 的首个回复。"""
    return _load(paths.replies / f"{validate_ask_id(ask_id)}.json")


def _correlated_message(ask: Message, paths: BusPaths) -> Message | None:
    """从队列与归档兜底查找回复或总线拒收回执。"""
    for directory in (paths.queue, paths.processed):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                candidate = read_message(path)
            except MalformedMessage:
                continue
            valid_sender = candidate.sender == ask.to or candidate.sender == "bus"
            if (
                candidate.reply_to == ask.id
                and candidate.to == ask.sender
                and candidate.kind in {"reply", "receipt"}
                and valid_sender
            ):
                return candidate
    return None


def wait_for_reply(
    ask_id: str,
    paths: BusPaths,
    *,
    timeout: float = DEFAULT_ASK_TIMEOUT_SECONDS,
    poll_interval: float = ASK_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Message | None:
    """等待关联回复;超时返回 ``None``。"""
    if timeout < 0:
        raise AskError("等待超时不能是负数")
    ask = load_ask(ask_id, paths)
    if ask is None:
        raise AskError(f"找不到 ask {ask_id}")

    deadline = clock() + timeout
    while True:
        reply = load_reply(ask_id, paths) or _correlated_message(ask, paths)
        if reply is not None:
            return reply
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(poll_interval, remaining))
