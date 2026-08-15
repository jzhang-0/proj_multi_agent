"""bus 根目录布局与路径注入。

默认根目录是仓库根的 `bus/`(与 v0 一致),可以由调用方显式传入,
也可以用环境变量 `BUS_ROOT` 覆盖。测试一律注入临时目录,不碰仓库根。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 仓库根的判定标记,自底向上找到第一个含有其中任一文件的目录
_ROOT_MARKERS = ("pyproject.toml", "AGENTS.md")

ENV_BUS_ROOT = "BUS_ROOT"


def repo_root() -> Path:
    """自 `src/bus/paths.py` 向上找仓库根;找不到就退回当前工作目录。"""
    for candidate in Path(__file__).resolve().parents:
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return Path.cwd()


@dataclass(frozen=True)
class BusPaths:
    """bus 运行时目录。所有子目录都从 `root` 派生,便于整体重定向。"""

    root: Path

    @classmethod
    def resolve(cls, root: str | os.PathLike[str] | None = None) -> BusPaths:
        """按 `显式参数 > BUS_ROOT 环境变量 > 仓库根/bus` 的优先级确定根目录。"""
        if root is not None:
            return cls(Path(root).expanduser().resolve())
        env_root = os.environ.get(ENV_BUS_ROOT)
        if env_root:
            return cls(Path(env_root).expanduser().resolve())
        return cls(repo_root() / "bus")

    @property
    def queue(self) -> Path:
        """待投递消息,一条一个 json 文件。"""
        return self.root / "queue"

    @property
    def processed(self) -> Path:
        """已处理完成(投递成功或明确失败)的消息归档。"""
        return self.root / "processed"

    @property
    def dead(self) -> Path:
        """死信:结构非法、读不出来的消息,附带 `.err` 说明文件。"""
        return self.root / "dead"

    @property
    def log(self) -> Path:
        """审计日志(schema 由 BUS-008 统一)。"""
        return self.root / "log.jsonl"

    def ensure(self) -> BusPaths:
        """创建全部子目录,幂等。"""
        for directory in (self.queue, self.processed, self.dead):
            directory.mkdir(parents=True, exist_ok=True)
        return self
