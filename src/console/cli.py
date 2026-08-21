"""总控台命令行。

`amux` 裸跑起全屏 TUI;`amux --headless` 就是纯 hub 模式
(直接交给 `bus.headless`,与 `python3 hub.py` 同一份实现,行为不会漂)。
`amux workspace add|list|rm|current|gc|migrate` 管理工作区登记;
`amux member add|rm|list` 增减当前工作区成员;`amux msg` 从当前目录定位工作区总线。
`amux --workspace <slug>` 显式绑定工作区(默认从 cwd 向上解析;未登记则自动登记当前目录)。

`uv run console` 是同一个入口的别名,历史 Goal 证据里的命令继续可用。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from bus.paths import BusPaths
from console import __version__
from roster.schema import RosterError
from tmuxctl import TmuxError
from workspace.errors import SlugError, WorkspaceError, WorkspaceNotFound
from workspace.global_config import load_global_config
from workspace.model import Workspace
from workspace.resolve import ensure_from_cwd, require_slug, resolve_from_cwd


def build_parser(*, default_theme: str = "console-dark") -> argparse.ArgumentParser:
    """构造总控台命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="amux",
        description="本机多 AI 群聊与指挥中心",
        epilog=(
            "工作区: amux workspace add|list|rm|current|gc|migrate; "
            "成员: amux member add|rm|list; "
            "配置: amux config init|show; "
            "发消息: amux msg <名字> <内容>"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="不起界面,只跑投递循环(等价于 python3 hub.py)",
    )
    parser.add_argument(
        "--bus-root",
        default=None,
        help="bus 运行时根目录(默认当前工作区或仓库根 bus/)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        metavar="名字",
        help="显式指定工作区 slug(默认从当前目录向上解析)",
    )
    parser.add_argument(
        "--theme",
        choices=("console-dark", "console-light"),
        default=default_theme,
        help="启动主题(界面里按 t 随时切换)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅 --headless:清一次队列就退出",
    )
    parser.add_argument(
        "--no-fit",
        action="store_true",
        help="不要把成员 tmux 窗口调成主画面大小(默认会调,好让画面铺满)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """起总控台并返回进程退出码。"""
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "workspace":
        from workspace.cli import main as workspace_main

        return workspace_main(raw[1:])
    if raw and raw[0] == "member":
        from workspace.member_cli import main as member_main

        return member_main(raw[1:])
    if raw and raw[0] == "config":
        from workspace.global_config import main as config_main

        return config_main(raw[1:])
    if raw and raw[0] == "msg":
        from bus.cli import main as msg_main

        try:
            if "--bus-root" not in raw:
                ensure_from_cwd()
        except (WorkspaceError, SlugError, OSError) as exc:
            print(f"[amux] {exc}", file=sys.stderr)
            return 1
        return msg_main(raw[1:])
    try:
        global_config = load_global_config()
    except WorkspaceError as exc:
        print(f"[amux] {exc}", file=sys.stderr)
        return 1
    args = build_parser(default_theme=global_config.theme).parse_args(raw)

    try:
        paths, workspace = bind_runtime(bus_root=args.bus_root, slug=args.workspace)
    except (WorkspaceNotFound, WorkspaceError, SlugError) as exc:
        print(f"[amux] {exc}", file=sys.stderr)
        return 1

    if global_config.auto_start_members and workspace is not None:
        try:
            _auto_start_members(workspace)
        except (OSError, RosterError, TmuxError) as exc:
            print(f"[amux] 自动拉起成员失败: {exc}", file=sys.stderr)
            return 1

    if args.headless:
        from bus.headless import main as run_hub

        forwarded = ["--bus-root", str(paths.root)]
        if args.once:
            forwarded.append("--once")
        return run_hub(forwarded)

    if args.once:
        print("--once 只在 --headless 下有意义")
        return 1

    from console.app import ConsoleApp
    from console.theme import use as use_theme

    use_theme(args.theme)
    ConsoleApp(paths, workspace=workspace, fit_windows=not args.no_fit).run()
    return 0


def bind_runtime(
    *,
    bus_root: str | None = None,
    slug: str | None = None,
    cwd: str | Path | None = None,
) -> tuple[BusPaths, Workspace | None]:
    """选工作区并定总线根:`--bus-root` > `--workspace` > 当前目录(未登记则自动登记)。"""
    if slug:
        workspace: Workspace | None = require_slug(slug)
    elif bus_root is not None:
        workspace = resolve_from_cwd(cwd)
    else:
        workspace = ensure_from_cwd(cwd)
    if bus_root is not None:
        return BusPaths.resolve(bus_root), workspace
    assert workspace is not None
    return BusPaths.for_workspace(workspace), workspace


def _auto_start_members(workspace: Workspace) -> None:
    """全局配置明确要求时,幂等地启动当前工作区的有效成员。"""
    from roster.lifecycle import Lifecycle
    from roster.load import load_effective_roster
    from workspace.session import SessionNames, bind_tmux

    roster = load_effective_roster(cwd=workspace.project_root)
    if not roster.enabled_members():
        return
    tmux = bind_tmux(names=SessionNames(slug=workspace.slug))
    Lifecycle(roster, tmux, cwd=workspace.project_root).up()
