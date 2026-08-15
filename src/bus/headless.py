"""无界面 hub:`python3 hub.py` 与 `console --headless` 共用的那条投递循环。

本窗口就是人看的"群聊记录":全部流量按 v0 的格式打印出来,同时按
BUS-008 落审计日志。上屏一律走 `format_for_screen`(清洗过,见 BUS-005)。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bus.hub import DeliveryOutcome, DeliveryResult, Hub
from bus.paths import BusPaths
from bus.sanitize import format_for_screen

#: 每种结果在群聊记录里的标记(v0 的 ★/✓/✗ 语义保持不变)
MARKS = {
    DeliveryOutcome.SHOWN: "★",
    DeliveryOutcome.DELIVERED: "✓",
    DeliveryOutcome.FAILED: "✗ 投递失败",
    DeliveryOutcome.REJECTED: "⊘ 已拒收",
    DeliveryOutcome.MALFORMED: "☠ 畸形消息进死信",
}


def print_result(result: DeliveryResult) -> None:
    if result.message is None:
        print(f"{MARKS[result.outcome]}: {result.path.name} — {result.detail}", flush=True)
        return
    detail = f"({result.detail})" if result.detail else ""
    print(
        f"{format_for_screen(result.message)}  {MARKS[result.outcome]}{detail}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hub", description="本机多 AI 消息 hub")
    parser.add_argument("--bus-root", default=None, help="bus 运行时根目录(默认仓库根 bus/)")
    parser.add_argument("--once", action="store_true", help="清空一次队列就退出(脚本/测试用)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = BusPaths.resolve(args.bus_root).ensure()
    hub = Hub(paths, on_result=print_result)

    if args.once:
        hub.drain_once()
        return 0

    print(f"[hub] 已启动,监听 {paths.queue}", flush=True)
    print("[hub] 收件人名字 = tmux 会话名;发给 human 的消息只在本窗口显示\n", flush=True)
    try:
        hub.run()
    except KeyboardInterrupt:
        print("\n[hub] 已退出", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
