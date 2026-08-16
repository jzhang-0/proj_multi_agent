"""总控台命令行。

`amux` 裸跑起全屏 TUI;`amux --headless` 就是纯 hub 模式
(直接交给 `bus.headless`,与 `python3 hub.py` 同一份实现,行为不会漂)。
`amux workspace add|list|rm|current` 管理工作区登记。

`uv run console` 是同一个入口的别名,历史 Goal 证据里的命令继续可用。
bus 与 roster 的路径都从本文件位置向上找仓库根(见 `bus.paths.repo_root`),
所以装成全局命令后在任何目录下跑,指向的都是同一份运行时数据。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bus.paths import BusPaths
from console import __version__


def build_parser() -> argparse.ArgumentParser:
    """构造总控台命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="amux",
        description="本机多 AI 群聊与指挥中心",
        epilog="工作区: amux workspace add|list|rm|current",
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
    parser.add_argument("--bus-root", default=None, help="bus 运行时根目录(默认仓库根 bus/)")
    parser.add_argument(
        "--theme",
        choices=("console-dark", "console-light"),
        default="console-dark",
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
    args = build_parser().parse_args(raw)

    if args.headless:
        from bus.headless import main as run_hub

        forwarded = []
        if args.bus_root is not None:
            forwarded += ["--bus-root", args.bus_root]
        if args.once:
            forwarded.append("--once")
        return run_hub(forwarded)

    if args.once:
        print("--once 只在 --headless 下有意义")
        return 1

    from console.app import ConsoleApp
    from console.theme import use as use_theme

    use_theme(args.theme)
    ConsoleApp(BusPaths.resolve(args.bus_root), fit_windows=not args.no_fit).run()
    return 0
