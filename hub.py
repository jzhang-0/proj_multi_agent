#!/usr/bin/env python3
"""本机多 AI 消息 hub —— v0 入口,用法不变:`python3 hub.py`。

监听 `bus/queue/`,按收件人名字找到同名 tmux 会话,把消息"打字"进那个
终端并回车;全部流量打印在本窗口(这就是人看的群聊记录),同时落审计
日志。收件人为 `human` 的消息不投递,只在这里显示。

实现在 `src/bus/headless.py`,这里和 `msg` 一样只负责切到 Python 3.11+
的项目环境。可选参数:`--bus-root <目录>`、`--once`(清一次队列就退出)。
"""

import os
import sys

root = os.path.dirname(os.path.abspath(__file__))

if sys.version_info < (3, 11):
    os.execvp(
        "uv",
        (
            "uv",
            "run",
            "--project",
            root,
            "python",
            os.path.abspath(__file__),
            *sys.argv[1:],
        ),
    )

sys.path.insert(0, os.path.join(root, "src"))

main = __import__("bus.headless", fromlist=["main"]).main
raise SystemExit(main(sys.argv[1:]))
