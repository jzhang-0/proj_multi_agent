"""`uv run python -m gateway`:把手机接进群。

跑起来会打印一个带 token 的地址,手机在同一个 WiFi 下用浏览器打开就是
群聊页。消息进出都经 `bus/queue`,清洗、限频、熔断照常生效。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Sequence

from bus.paths import BusPaths
from gateway.base import Gateway
from gateway.config import GatewayConfig
from gateway.local import LocalChatAdapter, lan_address


def members_provider() -> Sequence[str]:
    """成员名来自名册;读不出来就给个空列表,由路由提示。"""
    try:
        from roster.load import load_roster

        return [member.name for member in load_roster().enabled_members()]
    except Exception:
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gateway", description="自建 IM 网关")
    parser.add_argument("--bus-root", default=None, help="bus 运行时根目录")
    parser.add_argument("--port", type=int, default=None, help="监听端口(默认读配置)")
    parser.add_argument("--host", default=None, help="监听地址(默认读配置)")
    return parser


async def serve(config: GatewayConfig, paths: BusPaths) -> None:
    adapter = LocalChatAdapter(config)
    gateway = Gateway(adapter, paths, members=members_provider, room=config.room)
    stop = asyncio.Event()
    task = asyncio.create_task(gateway.run(stop))
    await asyncio.sleep(0.2)  # 等服务真的起来再打印地址

    # flush:被 nohup / 管道接走时也要立刻看到地址,否则人不知道该开哪个链接
    print(f"[gateway] 群聊页:{config.url_for(lan_address())}", flush=True)
    print(f"[gateway] 本机自测:{config.url_for('127.0.0.1')}", flush=True)
    print("[gateway] 手机连同一个 WiFi 打开上面第一个地址;Ctrl-C 退出", flush=True)
    try:
        await task
    except asyncio.CancelledError:
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = GatewayConfig.load().ensure_token()
    if args.port is not None:
        config = type(config)(config.host, args.port, config.token, config.room)
    if args.host is not None:
        config = type(config)(args.host, config.port, config.token, config.room)

    paths = BusPaths.resolve(args.bus_root).ensure()
    try:
        asyncio.run(serve(config, paths))
    except KeyboardInterrupt:
        print("\n[gateway] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
