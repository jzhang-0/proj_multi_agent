"""``./msg`` 的兼容命令行实现。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from typing import TextIO

from bus.ask import (
    DEFAULT_ASK_TIMEOUT_SECONDS,
    AskError,
    load_ask,
    store_ask,
    store_reply,
    wait_for_reply,
)
from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit

USAGE_HINT = '用法: msg <收件人> <内容...>  例如: ./msg bob "帮我跑一下测试"'


def build_parser() -> argparse.ArgumentParser:
    """构造保持普通用法兼容的参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="msg",
        usage=(
            "msg <收件人> <内容...> | msg --ask <收件人> <问题...> "
            "| msg --reply <ask-id> <答复...>"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ask", action="store_true", help="发送问题并阻塞等待关联回复")
    mode.add_argument("--reply", metavar="ASK_ID", help="回复指定 ask id")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_ASK_TIMEOUT_SECONDS,
        help="ask 等待秒数(默认 600)",
    )
    parser.add_argument("--bus-root", default=None, help="bus 运行时根目录(默认仓库根 bus/)")
    parser.add_argument("parts", nargs="*", help="收件人与正文,或 reply 正文")
    return parser


def _ordinary_message(to: str, text: str, sender: str) -> Message:
    """构造只有冻结四字段的普通消息。"""
    return Message(to=to, sender=sender, text=text, ts=time.strftime("%Y-%m-%d %H:%M:%S"))


def _submit_ask(to: str, text: str, sender: str, paths: BusPaths) -> Message:
    ask = Message.create(to, text, sender=sender, kind="ask")
    state = store_ask(ask, paths)
    try:
        deposit(ask, paths)
    except Exception:
        state.unlink(missing_ok=True)
        raise
    return ask


def _submit_reply(ask_id: str, text: str, sender: str, paths: BusPaths) -> Message:
    ask = load_ask(ask_id, paths)
    if ask is None:
        raise AskError(f"找不到 ask {ask_id}")
    reply = Message.create(
        ask.sender,
        text,
        sender=sender,
        kind="reply",
        reply_to=ask_id,
    )
    state = store_reply(reply, paths)
    try:
        deposit(reply, paths)
    except Exception:
        state.unlink(missing_ok=True)
        raise
    return reply


def main(
    argv: Sequence[str] | None = None,
    *,
    paths: BusPaths | None = None,
    sender: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """执行 msg 命令并返回退出码。"""
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    bus_paths = paths or BusPaths.resolve(args.bus_root)
    actual_sender = sender or os.environ.get("AGENT_NAME", "human")

    try:
        if args.reply is not None:
            if not args.parts:
                print(USAGE_HINT, file=output)
                return 1
            if args.timeout != DEFAULT_ASK_TIMEOUT_SECONDS:
                raise AskError("--timeout 只能与 --ask 一起使用")
            text = " ".join(args.parts)
            reply = _submit_reply(args.reply, text, actual_sender, bus_paths)
            print(f"[msg] {actual_sender} -> {reply.to}: 已回复 ask {args.reply}", file=output)
            return 0

        if len(args.parts) < 2:
            print(USAGE_HINT, file=output)
            return 1
        to, text = args.parts[0], " ".join(args.parts[1:])

        if not args.ask:
            if args.timeout != DEFAULT_ASK_TIMEOUT_SECONDS:
                raise AskError("--timeout 只能与 --ask 一起使用")
            deposit(_ordinary_message(to, text, actual_sender), bus_paths)
            print(f"[msg] {actual_sender} -> {to}: 已进入队列", file=output)
            return 0

        ask = _submit_ask(to, text, actual_sender, bus_paths)
        print(f"[msg] {actual_sender} -> {to}: ask {ask.id} 已进入队列,等待回复", file=output)
        reply = wait_for_reply(ask.id or "", bus_paths, timeout=args.timeout)
        if reply is None:
            print(f"[msg] ask {ask.id}: 等待回复超时({args.timeout:g} 秒)", file=errors)
            return 3
        print(f"[reply] {reply.sender}: {reply.text}", file=output)
        return 0 if reply.kind == "reply" else 4
    except (AskError, OSError) as exc:
        print(f"[msg] 错误: {exc}", file=errors)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
