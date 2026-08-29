"""TEAM-002：只追加账本、责任权限、评审/验收/接管状态流。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from team.binding import bind_team
from team.store import DEFAULT_TEAM_ID, TeamStore
from work import (
    LedgerCorruptionError,
    TaskStatus,
    WorkPermissionError,
    WorkService,
    WorkTransitionError,
    WorkValidationError,
)
from workspace.store import Store


@pytest.fixture
def service(tmp_path: Path) -> WorkService:
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "amux-home")
    workspace = store.add(project, slug="project")[0]
    teams = TeamStore(store.home)
    teams.init_default()
    bind_team(workspace, DEFAULT_TEAM_ID, teams=teams)
    return WorkService.for_workspace(workspace, teams=teams)


def _submit(service: WorkService, task_id: str, assignee: str) -> None:
    service.progress(assignee, task_id, "实现完成")
    service.add_evidence(assignee, task_id, "tests/test_feature.py · 3 passed")
    service.submit(assignee, task_id, "请验收")


def test_standard_flow_keeps_one_leader_responsible_until_human_report(
    service: WorkService,
) -> None:
    created = service.create("fable", "修登录页", description="复现并修复回归")
    assert created.task_id == "T-001"
    service.assign("fable", "T-001", "sonnet")
    _submit(service, "T-001", "sonnet")
    service.request_review("fable", "T-001", "opus", note="独立检查回归风险")
    service.review_pass("opus", "T-001", "实现和测试均可信")
    service.accept("fable", "T-001", "已亲自检查测试与风险")

    snapshot = service.snapshot()
    task = snapshot.get("T-001")
    assert task.status is TaskStatus.ACCEPTED
    assert task.leader == "fable"
    assert task.assignee == "sonnet"
    assert task.reviewer == "opus"
    assert task.accepted_by == "fable"
    assert task.evidence == ("tests/test_feature.py · 3 passed",)
    with pytest.raises(WorkPermissionError, match="任务 Leader fable"):
        service.report("sonnet", "T-001", "我宣布完成")

    service.report("fable", "T-001", "已向 human 汇报结果、证据和风险")
    completed = service.snapshot().get("T-001")
    assert completed.status is TaskStatus.COMPLETED
    assert completed.accepted_by == completed.leader == "fable"

    # 重读只能从事件恢复；没有一份可覆盖的 tasks.json。
    assert len(service.snapshot().events) == 9
    assert not (service.ledger.root / "tasks.json").exists()
    lines = service.ledger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    assert all(json.loads(line)["hash"] for line in lines)


def test_member_cannot_create_or_accept_and_reviewer_cannot_self_review(
    service: WorkService,
) -> None:
    with pytest.raises(WorkPermissionError, match="只有团队 Leader"):
        service.create("sonnet", "越权建任务")
    service.create("fable", "责任边界")
    service.assign("fable", "T-001", "sonnet")
    _submit(service, "T-001", "sonnet")
    with pytest.raises(WorkPermissionError, match="只有任务 Leader"):
        service.accept("sonnet", "T-001", "自行结项")
    with pytest.raises(WorkValidationError, match="不能与执行者相同"):
        service.request_review("fable", "T-001", "sonnet")
    with pytest.raises(WorkValidationError, match="Leader 应直接验收"):
        service.request_review("fable", "T-001", "fable")
    service.request_review("fable", "T-001", "opus")
    with pytest.raises(WorkPermissionError, match="指定评审者是 opus"):
        service.review_pass("sol", "T-001", "越权评审")


def test_return_reassign_and_takeover_preserve_every_attempt(service: WorkService) -> None:
    service.create("fable", "复杂修复")
    service.assign("fable", "T-001", "sol")
    service.block("sol", "T-001", "依赖接口不稳定")
    service.reassign("fable", "T-001", "luna", "改由快速模型试验")
    _submit(service, "T-001", "luna")
    service.request_review("fable", "T-001", "opus")
    service.review_return("opus", "T-001", "缺少边界样例")
    service.takeover(
        "fable",
        "T-001",
        reason="两位成员均未覆盖边界",
        scope="补状态机边界和回归测试",
        delivered="Sol 的阻塞分析、Luna 的初版实现和测试",
        verification="由 Fable 补测后亲自验收",
    )

    snapshot = service.snapshot()
    task = snapshot.get("T-001")
    assert task.assignee == "fable"
    assert task.status is TaskStatus.IN_PROGRESS
    takeover = snapshot.events[-1]
    assert takeover.data["previous_assignee"] == "luna"
    assert takeover.data["scope"] == "补状态机边界和回归测试"
    assert takeover.data["delivered"].startswith("Sol")
    assert takeover.data["verification"].startswith("由 Fable")
    assert [event.kind for event in snapshot.events][-3:] == [
        "review-requested",
        "review-returned",
        "takeover",
    ]


def test_parent_cannot_be_accepted_until_all_split_tasks_are_completed(
    service: WorkService,
) -> None:
    service.create("fable", "发布功能")
    child = service.split("fable", "T-001", "补回归测试")
    assert child.task_id == "T-002"
    assert service.snapshot().children("T-001")[0].parent_id == "T-001"

    service.assign("fable", "T-001", "sonnet")
    _submit(service, "T-001", "sonnet")
    with pytest.raises(WorkTransitionError, match="未完成子任务: T-002"):
        service.accept("fable", "T-001", "父任务先验收")

    service.assign("fable", "T-002", "opus")
    _submit(service, "T-002", "opus")
    service.accept("fable", "T-002", "子任务验收")
    service.report("fable", "T-002", "子任务已汇报")
    service.accept("fable", "T-001", "全部子任务完成")


def test_evidence_is_required_before_submit(service: WorkService) -> None:
    service.create("fable", "不能空口提交")
    service.assign("fable", "T-001", "sonnet")
    with pytest.raises(WorkTransitionError, match="还没有证据"):
        service.submit("sonnet", "T-001", "完成了")


def test_hash_chain_detects_rewriting_historical_event(service: WorkService) -> None:
    service.create("fable", "不可覆盖")
    text = service.ledger.path.read_text(encoding="utf-8")
    raw = [json.loads(line) for line in text.splitlines()]
    raw[0]["data"]["title"] = "被人改写"
    service.ledger.path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in raw) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerCorruptionError, match="哈希校验失败"):
        service.snapshot()
