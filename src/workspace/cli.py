"""`amux workspace add|list|rm|current|gc|migrate`。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from workspace.cleanup import kill_workspace_sessions, reclaim_orphans
from workspace.errors import WorkspaceError
from workspace.limits import warn_workspace_count
from workspace.resolve import require_from_cwd
from workspace.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux workspace",
        description="登记、列出、删除、查看或迁移当前工作区。状态在 ~/.amux,不写进用户项目。",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    add_p = sub.add_parser("add", help="把一个项目目录登记为工作区")
    add_p.add_argument("path", nargs="?", default=None, help="项目根(默认当前目录)")
    add_p.add_argument(
        "--slug",
        default=None,
        help="显式指定 slug(默认取目录名;重名自动加 -2、-3)",
    )
    sub.add_parser("list", help="列出已登记工作区")
    rm_p = sub.add_parser("rm", help="关掉该区会话并取消登记(不碰项目文件)")
    rm_p.add_argument("slug", help="要删除的 slug")
    sub.add_parser("current", help="显示当前目录所属工作区")
    sub.add_parser("gc", help="回收已无登记但仍挂着的成员会话")
    migrate_p = sub.add_parser(
        "migrate",
        help="把仓库根 bus/ 拷进工作区总线(源目录先留着)",
    )
    migrate_p.add_argument("path", nargs="?", default=None, help="项目根(默认当前目录)")
    migrate_p.add_argument(
        "--rollback",
        action="store_true",
        help="把工作区总线拷回仓库根 bus/",
    )
    migrate_p.add_argument(
        "--force",
        action="store_true",
        help="目标已有数据时仍覆盖拷贝",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    store: Store | None = None,
    cwd: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """执行 workspace 子命令,返回退出码。"""
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    registry = store or Store.default()
    here = cwd or Path.cwd()
    try:
        if args.action == "add":
            return _cmd_add(args, registry, here, output)
        if args.action == "list":
            return _cmd_list(registry, output)
        if args.action == "rm":
            return _cmd_rm(args.slug, registry, output)
        if args.action == "current":
            return _cmd_current(registry, here, output)
        if args.action == "gc":
            return _cmd_gc(registry, output)
        if args.action == "migrate":
            return _cmd_migrate(args, registry, here, output)
    except (WorkspaceError, OSError) as exc:
        print(f"[workspace] {exc}", file=errors)
        return 1
    print("用法: amux workspace add|list|rm|current|gc|migrate", file=errors)
    return 2


def _cmd_add(args: argparse.Namespace, store: Store, cwd: Path, output: TextIO) -> int:
    target = Path(args.path) if args.path else cwd
    workspace, created = store.add(target, slug=args.slug)
    verb = "已登记" if created else "已存在"
    print(
        f"{verb}工作区 {workspace.slug}  →  {workspace.project_root}",
        file=output,
    )
    if created:
        print(f"状态目录 {workspace.state_dir}", file=output)
        warning = warn_workspace_count(store)
        if warning:
            print(f"[workspace] {warning}", file=output)
    return 0


def _cmd_list(store: Store, output: TextIO) -> int:
    items = store.list()
    if not items:
        print("还没有工作区。用 amux workspace add 登记一个项目。", file=output)
        return 0
    width = max(len(item.slug) for item in items)
    print(f"{'slug':<{width}}  path", file=output)
    for item in items:
        print(f"{item.slug:<{width}}  {item.project_root}", file=output)
    return 0


def _cmd_rm(slug: str, store: Store, output: TextIO) -> int:
    workspace = store.get(slug)
    if workspace is None:
        from workspace.errors import WorkspaceNotFound

        raise WorkspaceNotFound(f"没有叫 {slug!r} 的工作区")
    killed = _try_kill_workspace(slug)
    store.remove(slug)
    print(
        f"已删除工作区 {workspace.slug}(未改动项目文件 {workspace.project_root})",
        file=output,
    )
    if killed:
        print("已关闭会话: " + ", ".join(killed), file=output)
    return 0


def _cmd_gc(store: Store, output: TextIO) -> int:
    try:
        from workspace.session import bind_tmux

        tmux = bind_tmux()
    except Exception as exc:
        print(f"[workspace] 回收失败:没接上 tmux ({exc})", file=output)
        return 1
    killed = reclaim_orphans(tmux, store)
    if not killed:
        print("没有孤儿会话。", file=output)
        return 0
    print("已回收孤儿会话: " + ", ".join(killed), file=output)
    return 0


def _try_kill_workspace(slug: str) -> list[str]:
    try:
        from workspace.session import bind_tmux

        return kill_workspace_sessions(bind_tmux(), slug)
    except Exception:
        return []


def _cmd_migrate(
    args: argparse.Namespace, store: Store, cwd: Path, output: TextIO
) -> int:
    from workspace.migrate import migrate

    target = Path(args.path) if args.path else cwd
    print(
        migrate(target, store=store, rollback=args.rollback, force=args.force),
        file=output,
    )
    return 0


def _cmd_current(store: Store, cwd: Path, output: TextIO) -> int:
    workspace = require_from_cwd(cwd, store=store)
    print(f"{workspace.slug}  {workspace.project_root}", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
