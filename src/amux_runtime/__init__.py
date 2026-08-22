"""wheel 内置的运行时资源；源码权威文件由一致性测试约束到这里。"""

from __future__ import annotations

from importlib.resources import files

PROTOCOL_RESOURCE = "protocol.md"
ROSTER_RESOURCE = "roster.toml"
_ALLOWED = frozenset({PROTOCOL_RESOURCE, ROSTER_RESOURCE})


def read_resource(name: str) -> str:
    """读取打进 wheel 的受控文本资源。"""
    if name not in _ALLOWED:
        raise ValueError(f"未知 amux 运行时资源: {name}")
    return files(__package__).joinpath(name).read_text(encoding="utf-8")
