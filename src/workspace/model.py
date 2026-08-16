"""工作区数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    """一个已登记的工作区:项目根 + slug + 状态目录。"""

    slug: str
    project_root: Path
    state_dir: Path

    @property
    def meta_file(self) -> Path:
        """状态目录里记录项目根路径的源数据文件。"""
        return self.state_dir / "workspace.toml"
