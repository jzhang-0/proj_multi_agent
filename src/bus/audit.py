"""审计日志:每条消息的遭遇和 console 控制操作都留一行。

一行一个 JSON 对象追加进 `bus/log.jsonl`,字段固定,便于 `tail -f`、
`jq` 和总控台时间线回填(CON-003)。

两个取舍:

- **正文只存 80 字符预览,全文另存** `bus/bodies/<id>.txt`。日志要能长期
  `tail`、能全量读进时间线,不能被一条 32KB 的正文顶爆。
- **预览清洗、全文保留原样**。预览会被打到屏幕上,必须先过 `bus.sanitize`;
  全文是取证材料,原样留着才看得出对方到底塞了什么转义序列。

超过 10MB 轮转:现有文件改名成 `log-<时间戳>.jsonl`,新事件写进空的
`log.jsonl`,不删旧文件(留给人自己清)。
"""

from __future__ import annotations

import json
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from bus.message import TS_FORMAT, Message
from bus.paths import BusPaths
from bus.sanitize import sanitize

#: 单个日志文件的大小上限,超过就轮转
MAX_LOG_BYTES = 10 * 1024 * 1024

#: 日志里正文预览的字符数
PREVIEW_CHARS = 80


class AuditEvent(StrEnum):
    """一条消息可能被记下的事件。"""

    DEPOSIT = "deposit"  # 入队
    DELIVER = "deliver"  # 已注入收件人终端
    DELIVER_FAILED = "deliver-failed"  # 投递失败(会话不在等)
    REJECTED = "rejected"  # 被防环策略拒收,reason 里写原因
    MALFORMED = "malformed"  # 结构非法,进了死信目录
    CONTROL = "control"  # 人在 console 对成员执行控制动作


def preview_of(text: str) -> str:
    """清洗后截断到 80 字符,截断处补省略号。"""
    cleaned = sanitize(text)
    if len(cleaned) <= PREVIEW_CHARS:
        return cleaned
    return cleaned[:PREVIEW_CHARS] + "…"


class AuditLog:
    """`bus/log.jsonl` 的读写口。"""

    def __init__(self, paths: BusPaths, max_bytes: int = MAX_LOG_BYTES) -> None:
        self.paths = paths
        self.max_bytes = max_bytes

    @property
    def path(self) -> Path:
        return self.paths.log

    @property
    def bodies(self) -> Path:
        return self.paths.root / "bodies"

    def _store_body(self, message: Message) -> str | None:
        """把全文另存一份,返回相对 bus 根的路径;同一条消息只写一次。"""
        if not message.text:
            return None
        name = f"{message.id or preview_of(message.ts)}.txt"
        target = self.bodies / name
        if not target.exists():
            self.bodies.mkdir(parents=True, exist_ok=True)
            target.write_text(message.text, encoding="utf-8")
        return str(target.relative_to(self.paths.root))

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        rotated = self.path.with_name(f"log-{stamp}.jsonl")
        suffix = 1
        while rotated.exists():
            rotated = self.path.with_name(f"log-{stamp}-{suffix}.jsonl")
            suffix += 1
        self.path.rename(rotated)

    def _append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """轮转检查 + 追加一行,返回写下去的对象。"""
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def record(
        self,
        event: AuditEvent | str,
        message: Message,
        reason: str = "",
    ) -> dict[str, Any]:
        """记一条事件,返回落盘的那个 JSON 对象。"""
        entry: dict[str, Any] = {
            "ts": time.strftime(TS_FORMAT),
            "event": str(event),
            "from": message.sender,
            "to": message.to,
            "id": message.id,
            "preview": preview_of(message.text),
        }
        body = self._store_body(message)
        if body is not None:
            entry["body"] = body
        if message.kind is not None:
            entry["kind"] = message.kind
        if message.reply_to is not None:
            entry["replyTo"] = message.reply_to
        if reason:
            entry["reason"] = reason

        return self._stamp(entry)

    def record_malformed(self, path: Path, reason: str) -> dict[str, Any]:
        """畸形消息连 `Message` 都构不出来,只能按文件名记一条。"""
        entry: dict[str, Any] = {
            "ts": time.strftime(TS_FORMAT),
            "event": str(AuditEvent.MALFORMED),
            "from": None,
            "to": None,
            "id": None,
            "preview": preview_of(path.name),
            "reason": reason,
        }
        return self._stamp(entry)

    def record_control(
        self,
        action: str,
        target: str,
        *,
        changed: bool,
        detail: str = "",
    ) -> dict[str, Any]:
        """记录人对成员执行的控制操作，不伪装成总线消息。"""
        entry: dict[str, Any] = {
            "ts": time.strftime(TS_FORMAT),
            "event": str(AuditEvent.CONTROL),
            "from": "human",
            "to": target,
            "id": None,
            "preview": sanitize(action),
            "action": sanitize(action),
            "changed": changed,
        }
        if detail:
            entry["reason"] = sanitize(detail)
        return self._stamp(entry)

    def _stamp(self, entry: dict[str, Any]) -> dict[str, Any]:
        if self.paths.workspace is not None:
            entry["workspace"] = self.paths.workspace
        return self._append(entry)

    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        """读回日志(供时间线回填);读不懂的行跳过,不因为一行坏数据罢工。"""
        if not self.path.exists():
            return []
        parsed: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    parsed.append(item)
        return parsed[-limit:] if limit is not None else parsed

    def read_body(self, entry: dict[str, Any]) -> str | None:
        """按日志条目取回全文;没有全文或文件已被清掉时返回 None。"""
        body = entry.get("body")
        if not isinstance(body, str):
            return None
        target = self.paths.root / body
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")
