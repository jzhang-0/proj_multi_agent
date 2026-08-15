#!/usr/bin/env python3
"""兼容入口:必要时用 uv 切到 Python 3.11+,再调用 ``bus.cli``。"""

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

main = __import__("bus.cli", fromlist=["main"]).main
raise SystemExit(main())
