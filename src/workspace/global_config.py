"""`~/.amux/config.toml` 的全局默认配置与命令行入口。"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from workspace.errors import WorkspaceError
from workspace.paths import GLOBAL_CONFIG_NAME, amux_home

THEMES = frozenset({"console-dark", "console-light"})
DEFAULT_MEMBERS = ("claude", "codex", "cursor", "agy")


@dataclass(frozen=True)
class GlobalConfig:
    """不跟随项目的 amux 默认值。"""

    default_members: tuple[str, ...] = ()
    auto_start_members: bool = False
    theme: str = "console-dark"
    source: Path | None = None


def config_path(home: str | Path | None = None) -> Path:
    """配置文件位置;测试可传临时 ``AMUX_HOME``。"""
    root = Path(home).expanduser().resolve() if home is not None else amux_home()
    return root / GLOBAL_CONFIG_NAME


def load_global_config(home: str | Path | None = None) -> GlobalConfig:
    """读取全局配置;文件不存在时保持历史默认行为。"""
    target = config_path(home)
    if not target.is_file():
        return GlobalConfig()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"无法解析 {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{target} 根必须是表")
    unknown = sorted(set(raw) - {"workspace", "lifecycle", "console"})
    if unknown:
        raise WorkspaceError(f"{target} 含未知字段: {', '.join(unknown)}")
    workspace = _table(raw, "workspace", target)
    lifecycle = _table(raw, "lifecycle", target)
    console = _table(raw, "console", target)
    return GlobalConfig(
        default_members=_members(workspace, target),
        auto_start_members=_boolean(lifecycle, "auto_start_members", False, target),
        theme=_theme(console, target),
        source=target,
    )


def init_global_config(*, home: str | Path | None = None, force: bool = False) -> Path:
    """写入显式启用四个预设、自动拉起的首份全局配置。"""
    target = config_path(home)
    if target.exists() and not force:
        raise WorkspaceError(f"全局配置已存在: {target}。如需重写请加 --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux config",
        description="管理 ~/.amux/config.toml 的全局默认值",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="写入默认四成员、自动拉起、深色主题的配置")
    init.add_argument("--force", action="store_true", help="覆盖已有配置")
    sub.add_parser("show", help="显示当前生效的全局配置")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        if args.action == "init":
            target = init_global_config(force=args.force)
            print(f"已写入全局配置: {target}", file=output)
            return 0
        config = load_global_config()
    except (WorkspaceError, OSError) as exc:
        print(f"[config] {exc}", file=errors)
        return 1
    print(f"配置: {config_path()}", file=output)
    print(f"默认成员: {', '.join(config.default_members) or '未设置'}", file=output)
    print(f"自动拉起成员: {'是' if config.auto_start_members else '否'}", file=output)
    print(f"默认主题: {config.theme}", file=output)
    return 0


def _table(raw: dict[str, object], name: str, target: Path) -> dict[str, object]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise WorkspaceError(f"{target} 的 [{name}] 必须是表")
    return value


def _members(raw: dict[str, object], target: Path) -> tuple[str, ...]:
    unknown = sorted(set(raw) - {"default_members"})
    if unknown:
        raise WorkspaceError(f"{target} 的 [workspace] 含未知字段: {', '.join(unknown)}")
    value = raw.get("default_members", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkspaceError(f"{target} 的 default_members 必须是字符串数组")
    names = tuple(item.strip() for item in value)
    if any(not name for name in names):
        raise WorkspaceError(f"{target} 的 default_members 不能含空字符串")
    dupes = sorted({name for name in names if names.count(name) > 1})
    if dupes:
        raise WorkspaceError(f"{target} 的 default_members 成员名重复: {', '.join(dupes)}")
    return names


def _boolean(raw: dict[str, object], name: str, default: bool, target: Path) -> bool:
    unknown = sorted(set(raw) - {name})
    if unknown:
        raise WorkspaceError(f"{target} 的 [lifecycle] 含未知字段: {', '.join(unknown)}")
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise WorkspaceError(f"{target} 的 {name} 必须是 true 或 false")
    return value


def _theme(raw: dict[str, object], target: Path) -> str:
    unknown = sorted(set(raw) - {"theme"})
    if unknown:
        raise WorkspaceError(f"{target} 的 [console] 含未知字段: {', '.join(unknown)}")
    value = raw.get("theme", "console-dark")
    if not isinstance(value, str) or value not in THEMES:
        choices = ", ".join(sorted(THEMES))
        raise WorkspaceError(f"{target} 的 theme 必须是以下之一: {choices}")
    return value


_DEFAULT_CONFIG = (
    "# amux 的全局默认值。工作区 members.toml 与项目 amux.toml 优先于这里的成员名单。\n"
    "\n"
    "[workspace]\n"
    'default_members = ["claude", "codex", "cursor", "agy"]\n'
    "\n"
    "[lifecycle]\n"
    "auto_start_members = true\n"
    "\n"
    "[console]\n"
    'theme = "console-dark"\n'
)
