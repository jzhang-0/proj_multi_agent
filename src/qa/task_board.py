"""TEAM-002 视觉夹具：稳定展示任务看板、证据、事件流与关联沟通。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from bus import Message, deposit
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.members import MemberStatusService
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from work import WorkService
from workspace.store import Store

MEMBERS = ("fable", "sonnet", "opus", "luna", "sol")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TEAM-002 任务主界面视觉夹具")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    project = root / "demo-project"
    project.mkdir(parents=True)
    store = Store(root / "amux-home")
    workspace = store.add(project, slug="demo")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    service = WorkService.for_workspace(workspace, teams=teams)

    service.create("fable", "修复登录页回归", description="补边界实现并完成视觉验收")
    service.assign("fable", "T-001", "sonnet")
    service.progress("sonnet", "T-001", "实现完成，等待独立评审")
    service.add_evidence("sonnet", "T-001", "pytest tests/test_login.py · 42 passed")
    service.add_evidence("sonnet", "T-001", "tests/evidence/login-page.png · 2 张截图")
    service.submit("sonnet", "T-001", "代码、测试与截图已提交")
    service.request_review("fable", "T-001", "opus")
    service.review_pass("opus", "T-001", "边界与回归均通过，建议 Leader 验收")

    service.create("fable", "排查并发写入失败")
    service.assign("fable", "T-002", "luna")
    service.block("luna", "T-002", "等待上游复现日志")

    service.create("fable", "更新发布说明")
    service.assign("fable", "T-003", "sol")
    service.add_evidence("sol", "T-003", "README.md 已同步")
    service.submit("sol", "T-003", "发布说明完成")
    service.accept("fable", "T-003", "内容与实际命令一致")
    service.report("fable", "T-003", "已向 human 汇报")

    paths = BusPaths.for_workspace(workspace).ensure()
    deposit(
        Message.create(
            "sonnet",
            "请补充登录失败的边界截图",
            sender="fable",
            task="T-001",
        ),
        paths,
    )
    status = MemberStatusService(MEMBERS)
    for name in MEMBERS:
        status.override_state(name, "idle")

    ConsoleApp(
        paths,
        workspace=workspace,
        work_service=service,
        deliver=lambda _message: True,
        members=MEMBERS,
        member_status=status,
        pump_enabled=False,
        fit_windows=False,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
