"""进程内 epoch/revision 计数。

`revision` 语义(架构 §3)：同一 epoch 内每个域从 0 起严格 +1，只在内容指纹
变化时才 bump——不持久化，进程重启即随新 epoch 归零。

`TimelineCache`(单槽全量时间线投影缓存)已下沉到 `control.timeline`(T-022)：
它是与 UI 无关的共享控制面组件，Web 需要时直接 `from control.timeline
import TimelineCache`，不在这里 re-export。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from bus import BusPaths
from control.timeline import TimelineEntry, log_fingerprint, work_fingerprint
from work import WorkSnapshot

DOMAINS = ("workspace", "team", "roster", "work", "timeline", "member", "health")


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
        log_fingerprint(paths),
        work_fingerprint(snapshot),
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
