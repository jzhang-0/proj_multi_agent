"""成员栏的数据来源:名册。

名册读不出来(文件缺失、格式错)不该把界面拖垮——总控台照样要能起来
看总线流量,所以这里出错就回一个空列表,由界面提示。
"""

from __future__ import annotations

from roster.load import load_roster
from roster.schema import RosterError


def member_names() -> tuple[str, ...]:
    """名册里启用的成员名,顺序与 `roster.toml` 一致。"""
    try:
        roster = load_roster()
    except (RosterError, OSError):
        return ()
    return tuple(member.name for member in roster.enabled_members())
