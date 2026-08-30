"""成员栏的数据来源:名册。

名册读不出来(文件缺失、格式错)不该把界面拖垮——总控台照样要能起来
看总线流量,所以这里出错就回一个空列表,由界面提示。
"""

from __future__ import annotations

from console.theme import STATUS_GLYPHS, status_presentation
from control.members import (
    MemberCardSnapshot,
    MemberStatusService,
    member_names,
    pending_counts,
)

__all__ = [
    "MemberCardSnapshot",
    "MemberStatusService",
    "STATUS_PRESENTATION",
    "member_names",
    "pending_counts",
    "relative_activity",
]


class _StatusPresentation:
    """按状态名取 (图形, 标签, 颜色);颜色现取,换主题立刻跟上。"""

    def __getitem__(self, state: str) -> tuple[str, str, str]:
        if state not in STATUS_GLYPHS:
            raise KeyError(state)
        return status_presentation(state)

    def __contains__(self, state: object) -> bool:
        return state in STATUS_GLYPHS

    def __iter__(self):
        return iter(STATUS_GLYPHS)

    def keys(self):
        return STATUS_GLYPHS.keys()

    def values(self):
        return [status_presentation(state) for state in STATUS_GLYPHS]

    def items(self):
        return [(state, status_presentation(state)) for state in STATUS_GLYPHS]

    def __len__(self) -> int:
        return len(STATUS_GLYPHS)

#: 状态 → (图形, 短标签, 颜色)。图形与标签是固定的,颜色跟着主题走,
#: 所以这里做成"取值函数"而不是常量表(`console.theme` 是唯一定义处)。
STATUS_PRESENTATION = _StatusPresentation()


def relative_activity(last_at: float | None, now: float) -> str:
    """把 epoch 时间戳格式化为紧凑的相对时间。"""
    if last_at is None:
        return "未活动"
    seconds = max(0, int(now - last_at))
    if seconds < 1:
        return "刚刚"
    if seconds < 60:
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}时前"
    return f"{hours // 24}天前"
