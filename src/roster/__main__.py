"""`roster` 命令 / `python -m roster`:成员生命周期入口,同时是 `./start.sh` 的实现。

两套用法并存:

- 新用法(ROS-003):`roster up|down|restart [名字]`,幂等,只管成员死活;
- v0 用法:`start.sh`(不带参数)= 拉起全部成员后把本窗口交给 hub,
  `start.sh stop` = 关掉全部,`start.sh <名字>` = 只拉一个。这三种用法一字不变。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from roster.lifecycle import Lifecycle, render
from roster.load import load_effective_roster
from roster.paths import repo_root
from roster.schema import RosterError
from workspace.resolve import ensure_from_cwd
from workspace.session import bind_tmux

ACTIONS = ("up", "down", "restart")

USAGE = "用法: roster up|down|restart [名字] | start.sh [stop|<名字>]"


def _exec_hub() -> None:
    """把本窗口交给 hub(v0 行为:群聊记录就在这个窗口里)。"""
    print()
    print("全部成员已拉起。本窗口是群聊记录(hub),Ctrl-C 退出(不影响成员会话)。")
    print("派活示例: amux msg claude 写一个fizzbuzz到fizzbuzz.py 写完让codex review 通过后向我汇报")
    print()
    os.execvp("uv", ["uv", "run", "python", str(repo_root() / "hub.py")])


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 2 or (args and args[0] not in ACTIONS and len(args) > 1):
        print(USAGE, file=sys.stderr)
        return 1

    try:
        ensure_from_cwd()
        roster = load_effective_roster()
    except (RosterError, OSError) as exc:
        print(f"[roster] {exc}", file=sys.stderr)
        return 1

    lifecycle = Lifecycle(roster, bind_tmux())
    try:
        if args and args[0] in ACTIONS:
            action = getattr(lifecycle, args[0])
            print(render(action(args[1] if len(args) == 2 else None)))
            return 0

        # --- 以下是 v0 的 start.sh 用法 ---
        if args == ["stop"]:
            print(render(lifecycle.down()))
            return 0
        if args:
            print(render(lifecycle.up(args[0])))
            return 0
        print(render(lifecycle.up()))
    except RosterError as exc:
        print(f"[roster] {exc}", file=sys.stderr)
        return 1

    _exec_hub()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
