"""`amux member add|rm|list`。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from roster.load import load_roster
from roster.schema import RosterError
from workspace.errors import WorkspaceError
from workspace.members import add_member, load_workspace_members, remove_member
from workspace.resolve import ensure_from_cwd
from workspace.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux member",
        description="增减当前工作区的成员。新工作区默认空;四个 CLI 只是预设。",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    add_p = sub.add_parser("add", help="启用一个预设或自定义成员")
    add_p.add_argument("name", help="成员短名")
    add_p.add_argument(
        "--command",
        default=None,
        help="自定义启动命令(省略则用 roster.toml 里的预设)",
    )
    add_p.add_argument("args", nargs="*", help="自定义命令的参数")
    rm_p = sub.add_parser("rm", help="从本工作区名单里拿掉(不关会话)")
    rm_p.add_argument("name", help="成员短名")
    sub.add_parser("list", help="列出本工作区已启用的成员")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    store: Store | None = None,
    cwd: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    registry = store or Store.default()
    here = cwd or Path.cwd()
    try:
        workspace = ensure_from_cwd(here, store=registry)
        presets = load_roster()
        if args.action == "add":
            member, created = add_member(
                workspace,
                args.name,
                presets=presets,
                command=args.command,
                args=args.args,
            )
            verb = "已加入" if created else "已在名单里"
            extra = "" if not args.command else f" ({member.command})"
            print(f"{verb} {member.name}{extra}  →  {workspace.slug}", file=output)
            return 0
        if args.action == "rm":
            member = remove_member(workspace, args.name, presets=presets)
            print(f"已从 {workspace.slug} 拿掉 {member.name}", file=output)
            return 0
        if args.action == "list":
            return _cmd_list(workspace, presets, output)
    except (WorkspaceError, RosterError, OSError) as exc:
        print(f"[member] {exc}", file=errors)
        return 1
    print("用法: amux member add|rm|list", file=errors)
    return 2


def _cmd_list(workspace, presets, output: TextIO) -> int:
    from roster.load import load_effective_roster

    roster = load_effective_roster(cwd=workspace.project_root)
    enabled = roster.enabled_members()
    stored = load_workspace_members(workspace)
    if not enabled:
        known = ", ".join(item.name for item in presets.members)
        print(
            f"{workspace.slug} 还没有成员。amux member add <名字> 加一个(预设: {known})",
            file=output,
        )
        return 0
    source = "members.toml" if stored is not None else "amux.toml"
    print(f"{workspace.slug} ({source})", file=output)
    for member in enabled:
        print(f"  {member.name}  {member.command}", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
