"""仓库根与默认名册路径。"""

from __future__ import annotations

from pathlib import Path

_ROOT_MARKERS = ("pyproject.toml", "AGENTS.md")
DEFAULT_FILENAME = "roster.toml"


def source_root() -> Path | None:
    """返回源码仓库根；安装后的 wheel 环境返回 `None`。"""
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / marker).is_file() for marker in _ROOT_MARKERS) and (
            candidate / DEFAULT_FILENAME
        ).is_file():
            return candidate
    return None


def repo_root() -> Path:
    """兼容旧入口：源码模式返回仓库根，wheel 模式回落当前目录。"""
    found = source_root()
    if found is not None:
        return found
    return Path.cwd()


def default_path() -> Path:
    return repo_root() / DEFAULT_FILENAME


def source_default_path() -> Path | None:
    """源码权威名册路径；wheel 环境没有该路径。"""
    found = source_root()
    return None if found is None else found / DEFAULT_FILENAME
