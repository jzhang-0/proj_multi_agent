"""视觉自验证取证:`uv run python -m qa.visual --goal CON-003`。

把「console 跑在 tmux 里 → 造点流量 → `capture-pane -p -e` 截画面 → 存档」
这套动作固定下来,免得每个人手敲一遍还踩同样的坑(截早了截到空屏、忘了
`-e` 拿不到颜色、忘了清理会话)。

**截出来只是素材,判断还得人/模型自己看**。判定清单见
`docs/quality/visual-check.md`,截取物存 `tests/baseline/`,Goal 证据里
引用路径并写清"看到了什么"。

跑完会核对一遍机器上原有的 tmux 会话没被带走——总控台的取证不该伤到
正在干活的成员。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit

#: 截图前等界面画完的上限(秒)
READY_DEADLINE_S = 15.0

#: 轮询间隔(秒)
POLL_S = 0.2

#: 截取物存放目录(相对仓库根)
BASELINE_DIR = Path("tests") / "baseline"


def repo_root() -> Path:
    return BusPaths.resolve().root.parent


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def sessions() -> set[str]:
    result = tmux("list-sessions", "-F", "#{session_name}", check=False)
    return {line for line in result.stdout.splitlines() if line}


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.partition("x")
    return int(width), int(height)


def capture(session: str, *, colored: bool) -> str:
    args = ["capture-pane", "-p", "-t", session]
    if colored:
        args.append("-e")
    return tmux(*args).stdout


def wait_until_drawn(session: str, marker: str) -> bool:
    """等界面真的画出来再截,不然截到的是空屏或半张。"""
    deadline = time.monotonic() + READY_DEADLINE_S
    while time.monotonic() < deadline:
        if marker in capture(session, colored=False):
            return True
        time.sleep(POLL_S)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qa.visual", description="总控台视觉自验证取证")
    parser.add_argument("--goal", required=True, help="Goal ID,例如 CON-003")
    parser.add_argument("--scene", default="default", help="场景名,进文件名")
    parser.add_argument("--size", default="120x30", help="终端尺寸,如 120x30 或 80x24")
    parser.add_argument("--keys", default="", help="截图前发的按键,逗号分隔,如 Down,Tab")
    parser.add_argument("--say", default="", help="截图前往总线发一条消息(发件人 human)")
    parser.add_argument("--to", default="human", help="--say 的收件人,默认 human(只上屏)")
    parser.add_argument("--keep", action="store_true", help="截完不关会话,方便继续手动看")
    args = parser.parse_args(argv)

    if shutil.which("tmux") is None:
        print("[visual] 找不到 tmux")
        return 2

    width, height = parse_size(args.size)
    goal = args.goal.lower()
    session = f"visual-{goal}-{uuid.uuid4().hex[:4]}"
    bus_root = Path(tempfile.mkdtemp(prefix="qa-visual-"))
    before = sessions()

    try:
        tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            str(width),
            "-y",
            str(height),
            "-c",
            str(repo_root()),
            f"uv run console --bus-root {bus_root}",
        )
        if not wait_until_drawn(session, "总控台"):
            print("[visual] 等不到界面画出来,截图作废")
            return 1

        if args.say:
            deposit(Message.create(args.to, args.say, sender="human"), BusPaths.resolve(bus_root))
        for key in (k.strip() for k in args.keys.split(",") if k.strip()):
            tmux("send-keys", "-t", session, key)
            time.sleep(0.3)
        time.sleep(1.0)  # 让最后一次重绘落地

        target_dir = repo_root() / BASELINE_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{goal}-{args.scene}-{width}x{height}"
        plain = target_dir / f"{stem}.txt"
        colored = target_dir / f"{stem}.ansi"
        plain.write_text(capture(session, colored=False), encoding="utf-8")
        colored.write_text(capture(session, colored=True), encoding="utf-8")

        print(f"[visual] 截取物:{plain.relative_to(repo_root())}")
        print(f"[visual] 带色版:{colored.relative_to(repo_root())}")
        print("[visual] 现在按 docs/quality/visual-check.md 的清单逐条看图,再写进 Goal 证据")
        print(plain.read_text(encoding="utf-8"))
        return 0
    finally:
        if not args.keep:
            tmux("kill-session", "-t", f"={session}", check=False)
        shutil.rmtree(bus_root, ignore_errors=True)
        leaked = before - sessions()
        if leaked:
            print(f"[visual] 警告:原有会话不见了 {sorted(leaked)}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
