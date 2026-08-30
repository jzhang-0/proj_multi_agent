"""把现有两种日志时间统一成可排序的 Unix epoch 秒。"""

from __future__ import annotations

from datetime import UTC, datetime

from bus.message import TS_FORMAT


def audit_timestamp(ts: str) -> float:
    """解析 bus/log.jsonl 的本机时区朴素时间。"""
    if not ts:
        return 0.0
    try:
        return datetime.strptime(ts, TS_FORMAT).astimezone().timestamp()
    except ValueError:
        return 0.0


def work_timestamp(ts: str) -> float:
    """解析工作账本的 UTC ISO-8601 时间。"""
    if not ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()
