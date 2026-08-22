"""从独立 prompts 资源生成按团队角色区分的成员运行时开场白。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from amux_runtime import PROMPT_RESOURCES, ROSTER_RESOURCE, read_prompt, read_resource
from roster.paths import source_root

COLLABORATION_PROTOCOL_HEADING = "## amux 协作协议"

# 0.1.x 兼容名；运行时已不再从 AGENTS.md 抽取提示词。
CHAT_PROTOCOL_HEADING = COLLABORATION_PROTOCOL_HEADING
_PROMPT_ROLES = frozenset(PROMPT_RESOURCES)
_ROLE_PROMPTS = frozenset({"leader", "member"})
_REQUIRED_FIELDS = {
    "common": frozenset({"name", "workspace_slug", "project_root"}),
    "leader": frozenset({"team_id", "model", "responsibility", "team_roster"}),
    "member": frozenset({"team_id", "leader_name", "model", "responsibility"}),
}
_REQUIRED_MARKERS = {
    "common": ("[群消息]", "amux msg", "amux msg --reply", "human"),
    "leader": ("唯一 Leader", "最终责任", "验收", "接管", "human"),
    "member": ("Leader", "证据", "评审", "不能代替 Leader", "反复失败"),
}
_STALE_RULES = (
    "本机 AI 群",
    "只在被 @ 时响应",
    "不超过 6 轮",
    "30 秒内最多发送 8 条",
    "32KB",
    "50 条未投递",
)


class ProtocolSourceError(ValueError):
    """提示词资源缺失、格式错误或静态名册维护了副本。"""


def extract_collaboration_protocol(markdown: str) -> str:
    """保留 0.1.x 的 AGENTS.md 章节抽取 API，不再用于运行时。"""
    lines = markdown.splitlines()
    headings = [
        index
        for index, line in enumerate(lines)
        if line.strip() == COLLABORATION_PROTOCOL_HEADING
    ]
    if len(headings) != 1:
        raise ProtocolSourceError(
            f"{COLLABORATION_PROTOCOL_HEADING} 必须且只能出现一次"
        )

    start = headings[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    body = "\n".join(lines[start:end]).strip()
    if body == COLLABORATION_PROTOCOL_HEADING:
        raise ProtocolSourceError(f"{COLLABORATION_PROTOCOL_HEADING} 不能为空")
    return body


def load_prompt(name: str, path: str | Path | None = None) -> str:
    """读取一份提示词；显式路径用于维护检查，默认读取 wheel 资源。"""
    if name not in _PROMPT_ROLES:
        raise ProtocolSourceError(f"未知提示词角色: {name}")
    if path is None:
        return read_prompt(name).strip()
    target = Path(path)
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProtocolSourceError(f"无法读取提示词: {target}: {exc}") from exc


def load_collaboration_protocol(path: str | Path | None = None) -> str:
    """0.1.x 兼容 API：返回公共提示词文件。"""
    return load_prompt("common", path)


# 保留发行版 0.1.x 已导出的名称，避免升级时破坏已有调用方。
extract_chat_protocol = extract_collaboration_protocol
load_chat_protocol = load_collaboration_protocol


def render_member_greeting(
    name: str,
    *,
    intro_template: str = "",
    protocol: str | None = None,
    workspace_slug: str | None = None,
    project_root: str | Path | None = None,
    team_id: str = "",
    role: str = "",
    leader_name: str = "",
    model: str = "",
    responsibility: str = "",
    team_roster: str = "",
) -> str:
    """用公共文件和可选角色文件拼装启动时传给 CLI 的开场白。"""
    if role and role not in _ROLE_PROMPTS:
        raise ProtocolSourceError(f"未知成员角色: {role}")
    slug, root = _workspace_values(
        workspace_slug=workspace_slug,
        project_root=project_root,
    )
    values = {
        "name": name,
        "workspace_slug": slug,
        "project_root": root,
        "team_id": team_id or "unknown",
        "leader_name": leader_name or "unknown",
        "model": model or "unknown",
        "responsibility": responsibility or "unknown",
        "team_roster": team_roster or "unknown",
    }
    intro = intro_template.replace("{NAME}", name).replace("{name}", name).strip()
    common = _render_template(protocol or load_prompt("common"), values, source="common")
    role_prompt = _render_template(load_prompt(role), values, source=role) if role else ""
    return "\n\n".join(part for part in (intro, common, role_prompt) if part)


def _render_template(template: str, values: dict[str, str], *, source: str) -> str:
    try:
        return template.format_map(values).strip()
    except KeyError as exc:
        raise ProtocolSourceError(
            f"提示词 {source} 使用了未知占位符: {exc.args[0]}"
        ) from exc
    except ValueError as exc:
        raise ProtocolSourceError(f"提示词 {source} 的占位符格式错误: {exc}") from exc


def _workspace_values(
    *,
    workspace_slug: str | None,
    project_root: str | Path | None,
) -> tuple[str, str]:
    slug, root = workspace_slug, project_root
    if slug is None or root is None:
        from workspace.resolve import resolve_from_cwd

        found = resolve_from_cwd(root)
        if found is not None:
            slug = slug or found.slug
            root = root or found.project_root
    return slug or "unknown", str(root) if root is not None else "unknown"


def check_single_source(
    *,
    prompt_dir: str | Path | None = None,
    roster_path: str | Path | None = None,
) -> None:
    """检查三份提示词可渲染，且静态名册没有另存提示正文。"""
    directory = Path(prompt_dir) if prompt_dir is not None else None
    for name, required in _REQUIRED_FIELDS.items():
        path = directory / f"{name}.md" if directory is not None else None
        prompt = load_prompt(name, path)
        missing = sorted(field for field in required if f"{{{field}}}" not in prompt)
        if missing:
            raise ProtocolSourceError(
                f"提示词 {name} 缺少占位符: {', '.join(missing)}"
            )
        missing_markers = [
            marker for marker in _REQUIRED_MARKERS[name] if marker not in prompt
        ]
        if missing_markers:
            raise ProtocolSourceError(
                f"提示词 {name} 缺少职责规则: {', '.join(missing_markers)}"
            )
        stale = next((rule for rule in _STALE_RULES if rule in prompt), None)
        if stale:
            raise ProtocolSourceError(f"提示词 {name} 含过时规则: {stale}")
        values = {field: "check" for field in _all_template_fields()}
        _render_template(prompt, values, source=name)

    if roster_path is not None:
        target = Path(roster_path)
        try:
            roster_text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProtocolSourceError(f"无法检查静态名册: {target}: {exc}") from exc
        source = str(target)
    elif (root := source_root()) is not None:
        target = root / "roster.toml"
        roster_text = target.read_text(encoding="utf-8")
        source = str(target)
    else:
        roster_text = read_resource(ROSTER_RESOURCE)
        source = f"package:amux_runtime/{ROSTER_RESOURCE}"
    try:
        raw = tomllib.loads(roster_text)
    except tomllib.TOMLDecodeError as exc:
        raise ProtocolSourceError(f"无法检查静态名册: {source}: {exc}") from exc

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


def _all_template_fields() -> frozenset[str]:
    return frozenset().union(*_REQUIRED_FIELDS.values())
