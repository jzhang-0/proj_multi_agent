"""把仓库根 `bus/` 迁进 `~/.amux/workspaces/<slug>/bus/`。

拍板:拷过去,源目录先留着;核对之后再提示可删。回退是把工作区总线拷回
仓库根 `bus/`。两条都是拷贝,谁都不自动删对方。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from bus.paths import BUS_DIRNAME, repo_root
from workspace.errors import WorkspaceError
from workspace.model import Workspace
from workspace.store import Store

MARKER = ".migrated-from"


def migrate(
    project_root: str | Path | None = None,
    *,
    store: Store | None = None,
    rollback: bool = False,
    force: bool = False,
) -> str:
    """执行一次迁移或回退,返回给人看的说明(含下一步那条命令)。"""
    registry = store or Store.default()
    root = Path(project_root).expanduser().resolve() if project_root else repo_root()
    workspace, _created = registry.add(root)
    legacy = root / BUS_DIRNAME
    target = workspace.state_dir / BUS_DIRNAME
    if rollback:
        return _rollback(legacy, target, workspace)
    return _upgrade(legacy, target, workspace, force=force)


def _upgrade(legacy: Path, target: Path, workspace: Workspace, *, force: bool) -> str:
    if not legacy.exists():
        raise WorkspaceError(f"没有仓库根 {legacy} 可迁")
    if _same(legacy, target):
        raise WorkspaceError("源和目标是同一个目录,不用迁")
    if _has_payload(target) and not force:
        raise WorkspaceError(
            f"工作区总线 {target} 已经有数据。确认要覆盖拷贝就加 --force"
        )
    copied = _copy_tree(legacy, target)
    (target / MARKER).write_text(f"{legacy}\n", encoding="utf-8")
    return (
        f"已把 {legacy} 拷进工作区 {workspace.slug} 的总线({copied} 项) → {target}。"
        f"核对无误后可删源目录: rm -rf {legacy}。"
        f"回退: amux workspace migrate --rollback"
    )


def _rollback(legacy: Path, target: Path, workspace: Workspace) -> str:
    if not target.exists():
        raise WorkspaceError(f"工作区 {workspace.slug} 还没有总线目录 {target}")
    if _same(legacy, target):
        raise WorkspaceError("源和目标是同一个目录,不用回退")
    copied = _copy_tree(target, legacy)
    return (
        f"已把工作区 {workspace.slug} 的总线拷回 {legacy}({copied} 项)。"
        f"工作区目录 {target} 没动,确认旧入口可用后再自行清理。"
    )


def _same(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _has_payload(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(child.name != MARKER for child in path.iterdir())


def _copy_tree(src: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in src.iterdir():
        if item.name == MARKER:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
        copied += 1
    return copied
