"""进程内 epoch/revision 计数与全量时间线投影缓存。

`revision` 语义(架构 §3)：同一 epoch 内每个域从 0 起严格 +1，只在内容指纹
变化时才 bump——不持久化，进程重启即随新 epoch 归零。`TimelineCache` 只是
避免 §4.7/§4.8 在同一份审计日志未变的情况下重复跑一次 `history_from_entries`
全量重建(fable 复审 T-003 时指出：2 万行审计单次重建约 0.28s，高频端点不该
每请求都算一遍)；它不做后台监视，仍是每请求按需、同步计算的一次性读。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from bus import BusPaths
from bus.audit import AuditLog
from control.timeline import TimelineEntry, history_from_entries
from work import WorkEvent, WorkSnapshot

DOMAINS = ("workspace", "team", "roster", "work", "timeline", "member", "health")


def _log_fingerprint(paths: BusPaths | None) -> tuple[int, int]:
    if paths is None or not paths.log.exists():
        return (0, 0)
    stat = paths.log.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _work_fingerprint(snapshot: WorkSnapshot | None) -> str | None:
    if snapshot is None or not snapshot.events:
        return None
    return snapshot.events[-1].digest


def timeline_revision_fingerprint(
    paths: BusPaths | None,
    snapshot: WorkSnapshot | None,
    projected: list[TimelineEntry],
) -> tuple[Any, ...]:
    """timeline 域 revision 的内容指纹。

    WEB-003 曾只用 ``(审计条数, 末条 key, 末条 outcome)``。任务事件按 ``at``
    插入时间线中间、末条仍是总线消息时，这三项都不变，revision 不 bump，
    实时 delta 会静默丢事件（opus T-013 陷阱 4）。协议 §3.2 要求账本追加
    同时 bump ``work`` 与 ``timeline``，故并入日志指纹、work 哈希链末端和
    全量投影长度。
    """
    return (
        _log_fingerprint(paths),
        _work_fingerprint(snapshot),
        len(projected),
        projected[-1].key if projected else "",
        projected[-1].outcome if projected else "",
    )


class RevisionTracker:
    """每域一个内存计数器；只比较指纹是否变化，不关心指纹本身的意义。"""

    def __init__(self) -> None:
        self._epoch = secrets.token_hex(8)
        self._epoch_started_at = time.time()
        self._fingerprints: dict[str, object] = {}
        self._revisions: dict[str, int] = dict.fromkeys(DOMAINS, 0)

    @property
    def epoch(self) -> str:
        return self._epoch

    @property
    def epoch_started_at(self) -> float:
        return self._epoch_started_at

    def revisions(self) -> dict[str, int]:
        return dict(self._revisions)

    def reset_epoch(self) -> None:
        """工作区重绑定等语义变化时整体换代；旧 revision 全部作废。"""
        self._epoch = secrets.token_hex(8)
        self._epoch_started_at = time.time()
        self._fingerprints.clear()
        self._revisions = dict.fromkeys(DOMAINS, 0)

    def observe(self, domain: str, fingerprint: object) -> int:
        """记录一次指纹观察，指纹变化才 bump，返回该域当前 revision。"""
        if domain not in self._revisions:
            raise ValueError(f"未知域: {domain}")
        if domain not in self._fingerprints:
            self._fingerprints[domain] = fingerprint
            return self._revisions[domain]
        if self._fingerprints[domain] != fingerprint:
            self._fingerprints[domain] = fingerprint
            self._revisions[domain] += 1
        return self._revisions[domain]


@dataclass(frozen=True)
class _TimelineCacheEntry:
    fingerprint: tuple[Any, ...]
    raw_entries: list[dict[str, Any]] = field(repr=False)
    entries: list[TimelineEntry] = field(repr=False)


class TimelineCache:
    """单槽缓存：审计日志与任务事件都未变时复用上一次的全量投影。"""

    def __init__(self) -> None:
        self._entry: _TimelineCacheEntry | None = None

    def get(
        self,
        paths: BusPaths,
        *,
        work_events: tuple[WorkEvent, ...],
        snapshot: WorkSnapshot | None,
    ) -> tuple[list[dict[str, Any]], list[TimelineEntry]]:
        fingerprint = (
            str(paths.root),
            _log_fingerprint(paths),
            _work_fingerprint(snapshot),
        )
        cached = self._entry
        if cached is not None and cached.fingerprint == fingerprint:
            return cached.raw_entries, cached.entries
        audit = AuditLog(paths)
        raw_entries = audit.entries()
        projected = history_from_entries(
            raw_entries,
            max(1, len(raw_entries) + len(work_events)),
            work_events=work_events,
            snapshot=snapshot,
        )
        self._entry = _TimelineCacheEntry(fingerprint, raw_entries, projected)
        return raw_entries, projected
