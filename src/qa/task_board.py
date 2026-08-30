"""TEAM-002/007 视觉夹具：任务看板、工作对话输入与图片附件。"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from bus import Attachment, Message, deposit
from bus.audit import AuditLog
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.clipboard import ClipboardImageStore
from console.compose import ComposeInput
from console.members import MemberStatusService
from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from work import WorkService
from workspace.store import Store

MEMBERS = ("fable", "sonnet", "opus", "luna", "sol")


class TaskComposeDemoApp(ConsoleApp):
    """取证专用：挂载后把 @ 候选与一张待发图片同时放到输入区。"""

    def __init__(self, *, demo_attachment: Attachment, **kwargs: object) -> None:
        self.demo_attachment = demo_attachment
        super().__init__(**kwargs)  # type: ignore[arg-type]

    def on_mount(self) -> None:
        super().on_mount()
        compose = self.query_one("#compose", ComposeInput)
        compose.attach_image(self.demo_attachment)
        compose.value = "@"
        compose.cursor_position = 1
        compose.refresh_candidates()
        compose.focus()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TEAM-002 任务主界面视觉夹具")
    parser.add_argument("--root", required=True)
    parser.add_argument("--compose-demo", action="store_true")
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

    # 审计日志只有秒级时间；跨过一个秒边界，确保随后四类对话在合并排序后
    # 位于任务事件之后，截图底部能同时看到分类，而不是全被 17 条任务淹没。
    time.sleep(1.05)
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
    deposit(
        Message.create(
            "fable",
            "请推进登录页并在完成后向我汇报",
            sender="human",
            task="T-001",
        ),
        paths,
    )
    deposit(
        Message.create(
            "human",
            "T-003 已完成，发布说明已经更新。",
            sender="fable",
            task="T-003",
        ),
        paths,
    )
    audit = AuditLog(paths)
    audit.record_control("key", "sonnet", changed=True, detail="Down")
    audit.record_control("type", "opus", changed=True, detail="请复审 T-001")
    status = MemberStatusService(MEMBERS)
    for name in MEMBERS:
        status.override_state(name, "idle")

    app_kwargs: dict[str, object] = {
        "paths": paths,
        "workspace": workspace,
        "work_service": service,
        "deliver": lambda _message: True,
        "members": MEMBERS,
        "member_status": status,
        "pump_enabled": False,
        "fit_windows": False,
    }
    if args.compose_demo:
        demo_image = Image.new("RGB", (800, 450), (32, 173, 212))
        demo_attachment = ClipboardImageStore(
            workspace.state_dir / "attachments",
            grabber=lambda: demo_image,
        ).paste()
        TaskComposeDemoApp(
            **app_kwargs,
            demo_attachment=demo_attachment,
        ).run()
    else:
        ConsoleApp(**app_kwargs).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
