"""工作区错误类型。"""

from __future__ import annotations


class WorkspaceError(ValueError):
    """工作区登记、解析或配置非法。"""


class WorkspaceNotFound(WorkspaceError):
    """从当前目录向上走,没有任何已登记的项目根。"""


class SlugError(WorkspaceError):
    """slug 含非法字符、为空,或显式指定时撞名。"""
