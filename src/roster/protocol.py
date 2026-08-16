"""从仓库 ``AGENTS.md`` 的「群聊协议」生成成员运行时开场白。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from roster.paths import repo_root

CHAT_PROTOCOL_HEADING = "## 群聊协议"


class ProtocolSourceError(ValueError):
    """群聊协议缺失、重复或静态名册又维护了副本。"""


def extract_chat_protocol(markdown: str) -> str:
    """抽取二级标题「群聊协议」的完整正文，不吞下后续章节。"""
    lines = markdown.splitlines()
    headings = [index for index, line in enumerate(lines) if line.strip() == CHAT_PROTOCOL_HEADING]
    if len(headings) != 1:
        raise ProtocolSourceError(f"{CHAT_PROTOCOL_HEADING} 必须且只能出现一次")

    start = headings[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    body = "\n".join(lines[start:end]).strip()
    if body == CHAT_PROTOCOL_HEADING:
        raise ProtocolSourceError(f"{CHAT_PROTOCOL_HEADING} 不能为空")
    return body


def load_chat_protocol(path: str | Path | None = None) -> str:
    """从指定或仓库根 ``AGENTS.md`` 读取权威群聊协议。"""
    target = Path(path) if path is not None else repo_root() / "AGENTS.md"
    try:
        markdown = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProtocolSourceError(f"无法读取群聊协议: {target}: {exc}") from exc
    return extract_chat_protocol(markdown)


def render_member_greeting(
    name: str,
    *,
    intro_template: str = "",
    protocol: str | None = None,
    workspace_slug: str | None = None,
    project_root: str | Path | None = None,
) -> str:
    """把成员身份、所在工作区和权威协议组合成启动时传给 CLI 的开场白。"""
    intro = intro_template.replace("{NAME}", name).replace("{name}", name).strip()
    identity = f"你是本机 AI 群的成员 {name}。"
    location = _workspace_location(workspace_slug=workspace_slug, project_root=project_root)
    if location:
        identity += location
    identity += "以下内容直接来自 AGENTS.md,必须遵守:"
    parts = [intro, identity, protocol or load_chat_protocol()]
    return "\n\n".join(part for part in parts if part)


def _workspace_location(
    *,
    workspace_slug: str | None,
    project_root: str | Path | None,
) -> str:
    slug, root = workspace_slug, project_root
    if slug is None or root is None:
        from workspace.resolve import resolve_from_cwd

        found = resolve_from_cwd()
        if found is not None:
            slug = slug or found.slug
            root = root or found.project_root
    if slug is None or root is None:
        return ""
    return f"你现在在工作区 {slug},项目根是 {root}。"


def check_single_source(
    *,
    agents_path: str | Path | None = None,
    roster_path: str | Path | None = None,
) -> None:
    """检查仓库静态名册没有另存一份开场白/协议文本,且协议已改用 amux msg。"""
    protocol = load_chat_protocol(agents_path)
    if "amux msg" not in protocol:
        raise ProtocolSourceError("群聊协议必须使用全局命令 amux msg")
    if "在仓库根运行 `./msg" in protocol:
        raise ProtocolSourceError("群聊协议不得再要求在仓库根运行 ./msg")
    target = Path(roster_path) if roster_path is not None else repo_root() / "roster.toml"
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProtocolSourceError(f"无法检查静态名册: {target}: {exc}") from exc

    if "default_greeting_template" in raw:
        raise ProtocolSourceError("roster.toml 不得维护 default_greeting_template 副本")
    members = raw.get("members", [])
    duplicates = [
        item.get("name", "<未知>")
        for item in members
        if isinstance(item, dict) and "greeting_template" in item
    ]
    if duplicates:
        raise ProtocolSourceError(
            "roster.toml 成员不得维护 greeting_template 副本: " + ", ".join(duplicates)
        )
