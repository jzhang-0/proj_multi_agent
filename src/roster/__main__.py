"""`python -m roster`:`./start.sh` 的实现。"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from roster.load import load_roster
from roster.paths import repo_root
from roster.schema import RosterError
from roster.start import start_all, start_member, stop_all
from tmuxctl import Tmux


def _print(lines: Sequence[str]) -> None:
    for line in lines:
        print(line)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        roster = load_roster()
    except RosterError as exc:
        print(f"[roster] {exc}", file=sys.stderr)
        return 1
    tmux = Tmux()
    if args == ["stop"]:
        _print(stop_all(roster, tmux))
        return 0
    if len(args) == 1:
        member = roster.get(args[0])
        if member is None:
            print(f"未知成员: {args[0]}", file=sys.stderr)
            return 1
        if not member.enabled:
            print(f"[start] {member.name} 已停用,跳过")
            return 0
        print(start_member(member, tmux))
        return 0
    if args:
        print("用法: start.sh [stop|<名字>]", file=sys.stderr)
        return 1
    _print(start_all(roster, tmux))
    print()
    print("全部成员已拉起。本窗口是群聊记录(hub),Ctrl-C 退出(不影响成员会话)。")
    print("派活示例: ./msg claude 写一个fizzbuzz到fizzbuzz.py 写完让codex review 通过后向我汇报")
    print()
    hub = repo_root() / "hub.py"
    os.execvp("uv", ["uv", "run", "python", str(hub)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
