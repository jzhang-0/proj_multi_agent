"""CON-007 视觉取证夹具：安全展示控制键位、详情与确认弹窗。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bus import BusPaths
from console.app import ConsoleApp
from console.control import ControlFeedback
from console.members import MemberStatusService
from tmuxctl import PaneSnapshot

MEMBERS = ("claude", "codex", "cursor", "agy")


class DemoSnapshotter:
    async def capture(self, target: str, *, color: bool = False, start=None) -> PaneSnapshot:
        if start is not None:
            text = (
                f"\x1b[33m↑ 回看 {target} 的 tmux 历史(start={start})\x1b[0m\n"
                "较早记录 03 · 已完成分析\n"
                "较早记录 02 · 正在修改代码\n"
                "较早记录 01 · 收到任务"
            )
        else:
            text = (
                f"\x1b[36m$ {target} agent\x1b[0m\n"
                "正在处理安全的视觉示例…\n"
                "输出只来自 QA 夹具。"
            )
        return PaneSnapshot(target, text, color, start, None, 0.0)


class DemoController:
    def _feedback(self, action: str, target: str) -> ControlFeedback:
        return ControlFeedback(action, target, True, "QA 夹具未操作真实成员")

    def interrupt(self, target: str) -> ControlFeedback:
        return self._feedback("interrupt", target)

    def terminate(self, target: str) -> ControlFeedback:
        return self._feedback("terminate", target)

    def restart(self, target: str) -> ControlFeedback:
        return self._feedback("restart", target)

    def takeover(self, target: str) -> ControlFeedback:
        return self._feedback("takeover", target)

    def press_key(self, target: str, key: str) -> ControlFeedback:
        return self._feedback("key", target)

    def record_failure(self, action: str, target: str, exc: Exception) -> None:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="成员控制视觉夹具")
    parser.add_argument("--bus-root", required=True)
    args = parser.parse_args(argv)
    paths = BusPaths.resolve(args.bus_root).ensure()
    status = MemberStatusService(MEMBERS)
    ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=MEMBERS,
        snapshotter=DemoSnapshotter(),
        member_status=status,
        pump_enabled=False,
        controller=DemoController(),
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
