"""CON-011 视觉取证夹具：安全展示三类故障和恢复提示。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bus import BusPaths
from console.app import ConsoleApp
from console.health import Fault, FaultEvent, FaultKind
from console.members import MemberStatusService
from tmuxctl import PaneSnapshot

MEMBERS = ("claude", "codex", "cursor", "agy")


class DemoSnapshotter:
    async def capture(self, target: str, *, color: bool = False, start=None) -> PaneSnapshot:
        return PaneSnapshot(target, "安全 QA 画面", color, start, None, 0.0)


class HealthDemoApp(ConsoleApp):
    def on_mount(self) -> None:
        # Textual 会沿 MRO 调用基类与子类 handler；这里不要手动 super,否则重复画启动提示。
        faults = (
            Fault(FaultKind.TMUX_SERVER, "tmux", "no server running"),
            Fault(FaultKind.MEMBER_SESSION, "codex", "成员 tmux 会话消失"),
            Fault(FaultKind.BUS_UNWRITABLE, "bus/queue", "PermissionError: read-only"),
        )
        for fault in faults:
            self._on_fault_event(FaultEvent(fault))
        for fault in faults:
            self._on_fault_event(FaultEvent(fault, recovered=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="错误与恢复视觉夹具")
    parser.add_argument("--bus-root", required=True)
    args = parser.parse_args(argv)
    paths = BusPaths.resolve(args.bus_root).ensure()
    HealthDemoApp(
        paths,
        deliver=lambda _message: True,
        members=MEMBERS,
        snapshotter=DemoSnapshotter(),
        member_status=MemberStatusService(MEMBERS),
        pump_enabled=False,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
