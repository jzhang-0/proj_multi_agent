"""项目侧可选 `amux.toml`。文件不存在即用默认,amux 不会替用户创建它。"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from workspace.errors import WorkspaceError
from workspace.paths import PROJECT_CONFIG_NAME

_KNOWN_KEYS = frozenset({"enabled", "env"})


@dataclass(frozen=True)
class ProjectConfig:
    """项目覆盖。`enabled is None` 表示没写,用全局默认名册全体成员。"""

    enabled: tuple[str, ...] | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    source: str | None = None

    @property
    def uses_defaults(self) -> bool:
        """没有项目侧文件(或文件是空表)时为 True。"""
        return self.source is None


def load_project_config(project_root: str | Path) -> ProjectConfig:
    """读 `<项目根>/amux.toml`;缺失则返回默认配置,不报错。"""
    target = Path(project_root) / PROJECT_CONFIG_NAME
    if not target.is_file():
        return ProjectConfig()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"无法解析 {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{target} 根必须是表")
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise WorkspaceError(f"{target} 含未知字段: {', '.join(unknown)}")
    return ProjectConfig(
        enabled=_optional_enabled(raw, target),
        env=_optional_env(raw, target),
        source=str(target),
    )


def _optional_enabled(raw: dict[str, object], target: Path) -> tuple[str, ...] | None:
    if "enabled" not in raw:
        return None
    value = raw["enabled"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkspaceError(f"{target} 的 enabled 必须是字符串数组")
    names = tuple(item.strip() for item in value)
    if any(not name for name in names):
        raise WorkspaceError(f"{target} 的 enabled 不能含空字符串")
    dupes = {name for name in names if names.count(name) > 1}
    if dupes:
        raise WorkspaceError(f"{target} 的 enabled 成员名重复: {', '.join(sorted(dupes))}")
    return names


def _optional_env(raw: dict[str, object], target: Path) -> dict[str, str]:
    if "env" not in raw:
        return {}
    value = raw["env"]
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise WorkspaceError(f"{target} 的 env 必须是字符串到字符串的表")
    return dict(value)
