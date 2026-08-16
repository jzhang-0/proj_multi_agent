"""bus 根目录布局与路径注入。

优先级:`显式参数` > `BUS_ROOT` 环境变量 > 当前工作区的
`~/.amux/workspaces/<slug>/bus` > 仓库根 `bus/`(未登记工作区时的 v0 回落)。
测试一律注入临时目录,不碰仓库根,也不碰用户的 `~/.amux`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workspace.model import Workspace
from workspace.resolve import resolve_from_cwd

#: 仓库根的判定标记,自底向上找到第一个含有其中任一文件的目录
_ROOT_MARKERS = ("pyproject.toml", "AGENTS.md")

ENV_BUS_ROOT = "BUS_ROOT"
BUS_DIRNAME = "bus"


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
    workspace: str | None = None

    @classmethod
    def resolve(
        cls,
        root: str | os.PathLike[str] | None = None,
        *,
        cwd: str | os.PathLike[str] | None = None,
    ) -> BusPaths:
        """按 `显式 > BUS_ROOT > 工作区 bus > 仓库根/bus` 确定根目录。"""
        if root is not None:
            return cls(Path(root).expanduser().resolve())
        env_root = os.environ.get(ENV_BUS_ROOT)
        if env_root:
            return cls(Path(env_root).expanduser().resolve())
        found = resolve_from_cwd(cwd)
        if found is not None:
            return cls.for_workspace(found)
        return cls(repo_root() / BUS_DIRNAME)

    @classmethod
    def for_workspace(cls, workspace: Workspace) -> BusPaths:
        """一个已登记工作区自己的总线根:`<state_dir>/bus`。"""
        return cls((workspace.state_dir / BUS_DIRNAME).resolve(), workspace=workspace.slug)

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
    def asks(self) -> Path:
        """等待回复的提问索引,文件名是 ask id。"""
        return self.root / "asks"

    @property
    def replies(self) -> Path:
        """ask 收到的首个关联回复,文件名是 ask id。"""
        return self.root / "replies"

    @property
    def log(self) -> Path:
        """审计日志(schema 由 BUS-008 统一)。"""
        return self.root / "log.jsonl"

    def ensure(self) -> BusPaths:
        """创建全部子目录,幂等。"""
        for directory in (self.queue, self.processed, self.dead, self.asks, self.replies):
            directory.mkdir(parents=True, exist_ok=True)
        return self
