"""总控台命令行。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行工程骨架入口并返回进程退出码。"""
    build_parser().parse_args(argv)
    print("总控台工程环境已就绪；当前群聊入口：./start.sh")
    return 0
