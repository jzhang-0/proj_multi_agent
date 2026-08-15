"""仓库根与默认名册路径。"""

from __future__ import annotations

from pathlib import Path

_ROOT_MARKERS = ("pyproject.toml", "AGENTS.md")
DEFAULT_FILENAME = "roster.toml"


def repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return Path.cwd()


def default_path() -> Path:
    return repo_root() / DEFAULT_FILENAME
