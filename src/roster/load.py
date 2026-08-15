"""加载 roster.toml。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from roster.paths import default_path
from roster.schema import Roster, RosterError, roster_from_dict


def load_roster(path: str | Path | None = None) -> Roster:
    """读并校验名册。默认仓库根 `roster.toml`。"""
    target = Path(path) if path is not None else default_path()
    if not target.is_file():
        raise RosterError(f"找不到名册文件: {target}")
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RosterError(f"无法解析 {target}: {exc}") from exc
    return roster_from_dict(raw, source=str(target))
