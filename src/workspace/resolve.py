"""从任意 cwd 向上找到所属工作区。"""

from __future__ import annotations

from pathlib import Path

from workspace.errors import WorkspaceNotFound
from workspace.model import Workspace
from workspace.store import Store


def resolve_from_cwd(
    cwd: str | Path | None = None,
    *,
    store: Store | None = None,
) -> Workspace | None:
    """从 cwd 向上走,命中已登记项目根则返回;找不到返回 None,不回落 amux 仓库。"""
    current = Path(cwd) if cwd is not None else Path.cwd()
    current = current.expanduser().resolve()
    index = (store or Store.default()).path_index()
    for candidate in (current, *current.parents):
        found = index.get(candidate)
        if found is not None:
            return found
    return None


def require_from_cwd(
    cwd: str | Path | None = None,
    *,
    store: Store | None = None,
) -> Workspace:
    """同 `resolve_from_cwd`,找不到就带「去 add」提示地报错。"""
    found = resolve_from_cwd(cwd, store=store)
    if found is None:
        here = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd()
        raise WorkspaceNotFound(
            f"当前目录不属于任何已登记工作区: {here}。"
            "把项目登记进来: amux workspace add"
        )
    return found


def require_slug(slug: str, *, store: Store | None = None) -> Workspace:
    """按 slug 取已登记工作区,没有就报错。"""
    found = (store or Store.default()).get(slug)
    if found is None:
        raise WorkspaceNotFound(
            f"没有叫 {slug!r} 的工作区。用 amux workspace list 看已登记的"
        )
    return found


def project_root_for_members(
    cwd: str | Path | None = None,
    *,
    store: Store | None = None,
) -> Path:
    """成员进程该落在的目录:已登记工作区用项目根,否则 amux 仓库根。"""
    found = resolve_from_cwd(cwd, store=store)
    if found is not None:
        return found.project_root
    from roster.paths import repo_root

    return repo_root()
