"""wheel 内置的名册和运行时提示词资源。"""

from __future__ import annotations

from importlib.resources import files

COMMON_PROMPT_RESOURCE = "prompts/common.md"
LEADER_PROMPT_RESOURCE = "prompts/leader.md"
MEMBER_PROMPT_RESOURCE = "prompts/member.md"
# 0.1.x 兼容名；公共协议已经迁入 prompts 目录。
PROTOCOL_RESOURCE = COMMON_PROMPT_RESOURCE
ROSTER_RESOURCE = "roster.toml"
PROMPT_RESOURCES = {
    "common": COMMON_PROMPT_RESOURCE,
    "leader": LEADER_PROMPT_RESOURCE,
    "member": MEMBER_PROMPT_RESOURCE,
}
_ALLOWED = frozenset({*PROMPT_RESOURCES.values(), ROSTER_RESOURCE})


def read_resource(name: str) -> str:
    """读取打进 wheel 的受控文本资源。"""
    if name not in _ALLOWED:
        raise ValueError(f"未知 amux 运行时资源: {name}")
    return files(__package__).joinpath(*name.split("/")).read_text(encoding="utf-8")


def read_prompt(role: str) -> str:
    """按通用/Leader/成员角色读取提示词文件。"""
    try:
        resource = PROMPT_RESOURCES[role]
    except KeyError as exc:
        raise ValueError(f"未知提示词角色: {role}") from exc
    return read_resource(resource)
