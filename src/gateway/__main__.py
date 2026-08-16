"""`uv run python -m gateway`:把手机接进群。

跑起来会打印一个带 token 的地址,手机在同一个 WiFi 下用浏览器打开就是
群聊页。消息进出都经 `bus/queue`,清洗、限频、熔断照常生效。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import replace

from bus.paths import BusPaths
from gateway.base import Gateway
from gateway.config import GatewayConfig
from gateway.local import LocalChatAdapter, lan_address
from gateway.security import PendingStore, SecurityPolicy


def members_provider() -> Sequence[str]:
    """成员名来自名册;读不出来就给个空列表,由路由提示。"""
    try:
        from roster.load import load_roster

        return [member.name for member in load_roster().enabled_members()]
    except Exception:
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="自建 IM 网关;不带子命令就是起服务",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="serve",
        choices=("serve", "pending", "approve", "reject"),
        help="serve 起服务;pending 列待确认;approve/reject 处置远程危险指令",
    )
    parser.add_argument("request_id", nargs="?", default=None, help="approve/reject 的编号")
    parser.add_argument("--bus-root", default=None, help="bus 运行时根目录")
    parser.add_argument("--port", type=int, default=None, help="监听端口(默认读配置)")
    parser.add_argument("--host", default=None, help="监听地址(默认读配置)")
    return parser


def handle_pending(action: str, request_id: str | None, paths: BusPaths) -> int:
    """本机侧的二次确认:远程指令必须过这一关(GATE-004)。"""
    store = PendingStore(paths)
    if action == "pending":
        items = store.entries()
        if not items:
            print("[gateway] 没有待确认的远程指令")
        for item in items:
            message = item.get("message", {})
            print(
                f"{item['id']}  {item['ts']}  {item['user']}(手机)→ {message.get('to')}"
                f"  「{item['label']}」  {message.get('text')}"
            )
        return 0
    if not request_id:
        print(f"[gateway] {action} 要给编号,先跑 pending 看有哪些")
        return 1
    ok = store.approve(request_id) if action == "approve" else store.reject(request_id)
    verb = "已放行,网关下一轮转给成员" if action == "approve" else "已拒绝并丢弃"
    print(f"[gateway] {request_id} {verb}" if ok else f"[gateway] 没有编号 {request_id}")
    return 0 if ok else 1


async def serve(config: GatewayConfig, paths: BusPaths) -> None:
    adapter = LocalChatAdapter(config)
    security = SecurityPolicy.from_config(config.rooms, config.users, config.room)
    gateway = Gateway(
        adapter,
        paths,
        members=members_provider,
        room=config.room,
        security=security,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(gateway.run(stop))
    await asyncio.sleep(0.2)  # 等服务真的起来再打印地址

    # flush:被 nohup / 管道接走时也要立刻看到地址,否则人不知道该开哪个链接
    print(f"[gateway] 群聊页:{config.url_for(lan_address())}", flush=True)
    print(f"[gateway] 本机自测:{config.url_for('127.0.0.1')}", flush=True)
    print("[gateway] 手机连同一个 WiFi 打开上面第一个地址;Ctrl-C 退出", flush=True)
    if config.users:
        print(f"[gateway] 白名单用户:{'、'.join(config.users)}", flush=True)
    else:
        print("[gateway] 白名单是空的,现在谁都服务不了;在 gateway.toml 里写 users", flush=True)
    try:
        await task
    except asyncio.CancelledError:
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # 只有起服务时才生成 token(approve/reject 不该顺手改配置文件)
    config = GatewayConfig.load()
    if args.action == "serve":
        config = config.ensure_token()
    if args.port is not None:
        config = replace(config, port=args.port)
    if args.host is not None:
        config = replace(config, host=args.host)

    paths = BusPaths.resolve(args.bus_root).ensure()
    if args.action != "serve":
        return handle_pending(args.action, args.request_id, paths)
    try:
        asyncio.run(serve(config, paths))
    except KeyboardInterrupt:
        print("\n[gateway] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
