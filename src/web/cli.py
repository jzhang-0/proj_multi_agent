"""`amux web` 入口(WEB-003):起本机 Web 控制台后端。

首版只做地基:本机认证会话 + 最小静态健康页,只监听 `127.0.0.1`。
workspace/team/work/timeline/member 的 snapshot 接口依赖 WEB-001 下沉的
控制面,尚未接入。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from web.app import create_app
from web.auth import WebSession

DEFAULT_PORT = 8787


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux web",
        description="起本机 Web 控制台后端(只监听 127.0.0.1,不自动漂移端口)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"监听端口(默认 {DEFAULT_PORT};被占用时请显式指定,不自动换端口)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        import uvicorn
    except ImportError:
        print(
            "[amux web] 缺少 uvicorn,请确认 amux-team 安装了默认 Web 依赖",
            file=sys.stderr,
        )
        return 1

    session = WebSession.generate()
    app = create_app(session=session, port=args.port)
    url = f"http://127.0.0.1:{args.port}/?token={session.token}"
    print(f"[amux web] 在浏览器打开: {url}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0
