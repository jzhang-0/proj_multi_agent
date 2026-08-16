"""WS-007 视觉取证夹具:两个工作区、不同成员、互不串台的时间线。"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from bus import BusPaths, Message, deposit
from console.app import ConsoleApp
from console.members import MemberStatusService
from workspace.model import Workspace
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store


def build_pair(root: Path) -> tuple[Workspace, Workspace]:
    """在 root 下登记 alpha / beta 两个演示工作区,并各塞一条只属于自己的消息。"""
    os.environ[ENV_AMUX_HOME] = str(root / "amux-home")
    store = Store.default()
    alpha_root = root / "projects" / "alpha"
    beta_root = root / "projects" / "beta"
    alpha_root.mkdir(parents=True, exist_ok=True)
    beta_root.mkdir(parents=True, exist_ok=True)
    (alpha_root / "amux.toml").write_text('enabled = ["claude", "codex"]\n', encoding="utf-8")
    (beta_root / "amux.toml").write_text('enabled = ["cursor"]\n', encoding="utf-8")
    alpha, _ = store.add(alpha_root, slug="alpha")
    beta, _ = store.add(beta_root, slug="beta")
    deposit(
        Message.create("human", "alpha 专属流量", sender="claude"),
        BusPaths.for_workspace(alpha).ensure(),
    )
    deposit(
        Message.create("human", "beta 专属流量", sender="cursor"),
        BusPaths.for_workspace(beta).ensure(),
    )
    return alpha, beta


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="多工作区控制台视觉夹具")
    parser.add_argument("--root", required=True, help="临时根目录(含 amux-home 与两个项目)")
    parser.add_argument("--slug", default="alpha", help="启动时绑定的工作区")
    args = parser.parse_args(argv)

    root = Path(args.root)
    alpha, beta = build_pair(root)
    bound = {"alpha": alpha, "beta": beta}[args.slug]
    members = tuple(
        {"alpha": ("claude", "codex"), "beta": ("cursor",)}[args.slug]
    )
    ConsoleApp(
        BusPaths.for_workspace(bound),
        workspace=bound,
        deliver=lambda _message: True,
        members=members,
        member_status=MemberStatusService(members),
        pump_enabled=False,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
