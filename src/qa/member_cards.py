"""CON-005 视觉取证夹具：同屏稳定展示五种成员状态。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bus import BusPaths, Message, deposit
from console.app import ConsoleApp
from console.members import MemberStatusService

MEMBERS = ("claude", "codex", "cursor", "agy", "legacy")
STATES = ("idle", "working", "stuck", "dead", "failed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="成员卡片五态视觉夹具")
    parser.add_argument("--bus-root", required=True)
    args = parser.parse_args(argv)

    paths = BusPaths.resolve(args.bus_root).ensure()
    status = MemberStatusService(MEMBERS)
    for index, (name, state) in enumerate(zip(MEMBERS, STATES, strict=True)):
        status.record_output(name, b"visual-activity")
        status.override_state(name, state)
        for queue_index in range(index):
            deposit(
                Message.create(name, f"visual queue {queue_index}", sender="human"),
                paths,
                audit=False,
            )

    ConsoleApp(
        paths,
        deliver=lambda _message: True,
        members=MEMBERS,
        member_status=status,
        pump_enabled=False,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
