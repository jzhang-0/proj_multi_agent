"""总控台命令行。

`uv run console` 起全屏 TUI;`uv run console --headless` 就是纯 hub 模式
(直接交给 `bus.headless`,与 `python3 hub.py` 同一份实现,行为不会漂)。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bus.paths import BusPaths
from console import __version__


def build_parser() -> argparse.ArgumentParser:
    """构造总控台命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="console",
        description="本机多 AI 群聊与指挥中心",
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
        "--once",
        action="store_true",
        help="仅 --headless:清一次队列就退出",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """起总控台并返回进程退出码。"""
    args = build_parser().parse_args(argv)

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

    ConsoleApp(BusPaths.resolve(args.bus_root)).run()
    return 0
