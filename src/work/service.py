"""TEAM-002 责任流：权限规则和允许的任务状态转换。"""

from __future__ import annotations

from collections.abc import Iterable

from team.binding import load_team_binding
from team.model import Team
from team.store import TeamStore
from work.ledger import PendingEvent, WorkLedger
from work.model import (
    EventKind,
    Task,
    TaskStatus,
    WorkEvent,
    WorkPermissionError,
    WorkSnapshot,
    WorkTransitionError,
    WorkValidationError,
    require_text,
)
from workspace.model import Workspace

ACTIVE_ASSIGNMENT_STATES = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.CHANGES_REQUESTED,
    }
)
FINAL_STATES = frozenset({TaskStatus.ACCEPTED, TaskStatus.COMPLETED})


class WorkService:
    """一个工作区、一个已绑定团队上的类型化任务操作。"""

    def __init__(
        self,
        workspace: Workspace,
        team: Team,
        *,
        ledger: WorkLedger | None = None,
    ) -> None:
        self.workspace = workspace
        self.team = team
        self.ledger = ledger or WorkLedger(workspace)
        self.member_ids = frozenset(member.id for member in team.members)

    @classmethod
    def for_workspace(
        cls,
        workspace: Workspace,
        *,
        teams: TeamStore | None = None,
    ) -> WorkService:
        binding = load_team_binding(workspace)
        if binding is None:
            raise WorkValidationError(
                f"工作区 {workspace.slug} 尚未绑定团队；先运行 amux team use <团队 ID>"
            )
        home = workspace.state_dir.parent.parent
        team = (teams or TeamStore(home)).load(binding.team_id)
        return cls(workspace, team)

    def snapshot(self) -> WorkSnapshot:
        return self.ledger.load()

    def create(self, actor: str, title: str, *, description: str = "") -> WorkEvent:
        self._require_current_leader(actor)
        clean_title = require_text(title, "任务标题")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            return PendingEvent(
                snapshot.next_task_id(),
                EventKind.CREATED,
                actor,
                {"title": clean_title, "description": description.strip(), "leader": actor},
            )

        return self.ledger.transact(build)[0]

    def split(
        self,
        actor: str,
        parent_id: str,
        title: str,
        *,
        description: str = "",
    ) -> WorkEvent:
        clean_title = require_text(title, "子任务标题")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            parent = snapshot.get(parent_id)
            self._require_task_leader(actor, parent)
            self._require_not_final(parent, "拆分")
            return PendingEvent(
                snapshot.next_task_id(),
                EventKind.SPLIT,
                actor,
                {
                    "title": clean_title,
                    "description": description.strip(),
                    "leader": parent.leader,
                    "parent": parent.id,
                },
            )

        return self.ledger.transact(build)[0]

    def assign(self, actor: str, task_id: str, assignee: str) -> WorkEvent:
        self._require_known_member(assignee)

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            if task.status is not TaskStatus.BACKLOG:
                raise WorkTransitionError(
                    f"{task.id} 当前为 {task.status}，首次派工只允许待派工任务"
                )
            return PendingEvent(task.id, EventKind.ASSIGNED, actor, {"assignee": assignee})

        return self.ledger.transact(build)[0]

    def progress(self, actor: str, task_id: str, summary: str) -> WorkEvent:
        return self._assignee_event(
            task_id,
            actor,
            EventKind.PROGRESS,
            "进展",
            {"summary": require_text(summary, "进展")},
        )

    def block(self, actor: str, task_id: str, reason: str) -> WorkEvent:
        return self._assignee_event(
            task_id,
            actor,
            EventKind.BLOCKED,
            "阻塞",
            {"reason": require_text(reason, "阻塞原因")},
        )

    def add_evidence(self, actor: str, task_id: str, reference: str) -> WorkEvent:
        return self._assignee_event(
            task_id,
            actor,
            EventKind.EVIDENCE,
            "提交证据",
            {"reference": require_text(reference, "证据")},
        )

    def submit(self, actor: str, task_id: str, summary: str) -> WorkEvent:
        clean = require_text(summary, "提交摘要")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_assignee(actor, task)
            self._require_status(task, ACTIVE_ASSIGNMENT_STATES, "提交")
            if not task.evidence:
                raise WorkTransitionError(f"{task.id} 还没有证据；先记录 evidence 再提交")
            return PendingEvent(task.id, EventKind.SUBMITTED, actor, {"summary": clean})

        return self.ledger.transact(build)[0]

    def request_review(
        self,
        actor: str,
        task_id: str,
        reviewer: str,
        *,
        note: str = "",
    ) -> WorkEvent:
        self._require_known_member(reviewer)

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            self._require_status(task, {TaskStatus.SUBMITTED}, "指定评审")
            if reviewer == task.assignee:
                raise WorkValidationError("评审者不能与执行者相同")
            if reviewer == task.leader:
                raise WorkValidationError("Leader 应直接验收，独立评审请指定另一名成员")
            return PendingEvent(
                task.id,
                EventKind.REVIEW_REQUESTED,
                actor,
                {"reviewer": reviewer, "note": note.strip()},
            )

        return self.ledger.transact(build)[0]

    def review_pass(self, actor: str, task_id: str, note: str) -> WorkEvent:
        return self._review_event(
            task_id,
            actor,
            EventKind.REVIEW_PASSED,
            {"note": require_text(note, "评审意见")},
        )

    def review_return(self, actor: str, task_id: str, reason: str) -> WorkEvent:
        return self._review_event(
            task_id,
            actor,
            EventKind.REVIEW_RETURNED,
            {"reason": require_text(reason, "退回原因")},
        )

    def reassign(self, actor: str, task_id: str, assignee: str, reason: str) -> WorkEvent:
        self._require_known_member(assignee)
        clean = require_text(reason, "重新分派原因")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            self._require_not_final(task, "重新分派")
            return PendingEvent(
                task.id,
                EventKind.REASSIGNED,
                actor,
                {"assignee": assignee, "previous_assignee": task.assignee, "reason": clean},
            )

        return self.ledger.transact(build)[0]

    def takeover(
        self,
        actor: str,
        task_id: str,
        *,
        reason: str,
        scope: str,
        delivered: str,
        verification: str,
    ) -> WorkEvent:
        data = {
            "reason": require_text(reason, "接管原因"),
            "scope": require_text(scope, "接管范围"),
            "delivered": require_text(delivered, "原成员已交付内容"),
            "verification": require_text(verification, "后续验收方式"),
        }

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            self._require_not_final(task, "接管")
            return PendingEvent(
                task.id,
                EventKind.TAKEOVER,
                actor,
                {**data, "previous_assignee": task.assignee},
            )

        return self.ledger.transact(build)[0]

    def accept(self, actor: str, task_id: str, conclusion: str) -> WorkEvent:
        clean = require_text(conclusion, "验收结论")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            self._require_status(task, {TaskStatus.SUBMITTED, TaskStatus.REVIEWED}, "验收")
            if not task.evidence:
                raise WorkTransitionError(f"{task.id} 没有证据，不能验收")
            unfinished = [child.id for child in snapshot.children(task.id) if not child.completed]
            if unfinished:
                raise WorkTransitionError(f"{task.id} 还有未完成子任务: {', '.join(unfinished)}")
            return PendingEvent(task.id, EventKind.ACCEPTED, actor, {"conclusion": clean})

        return self.ledger.transact(build)[0]

    def report(self, actor: str, task_id: str, summary: str) -> WorkEvent:
        clean = require_text(summary, "human 汇报摘要")

        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_task_leader(actor, task)
            self._require_status(task, {TaskStatus.ACCEPTED}, "向 human 汇报并结项")
            return PendingEvent(task.id, EventKind.REPORTED, actor, {"summary": clean})

        return self.ledger.transact(build)[0]

    def _assignee_event(
        self,
        task_id: str,
        actor: str,
        kind: EventKind,
        action: str,
        data: dict[str, str],
    ) -> WorkEvent:
        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            self._require_assignee(actor, task)
            self._require_status(task, ACTIVE_ASSIGNMENT_STATES, action)
            return PendingEvent(task.id, kind, actor, data)

        return self.ledger.transact(build)[0]

    def _review_event(
        self,
        task_id: str,
        actor: str,
        kind: EventKind,
        data: dict[str, str],
    ) -> WorkEvent:
        def build(snapshot: WorkSnapshot) -> PendingEvent:
            task = snapshot.get(task_id)
            if actor != task.reviewer:
                raise WorkPermissionError(
                    f"{task.id} 的指定评审者是 {task.reviewer or '尚未指定'}，不是 {actor}"
                )
            self._require_status(task, {TaskStatus.IN_REVIEW}, "评审")
            return PendingEvent(task.id, kind, actor, data)

        return self.ledger.transact(build)[0]

    def _require_current_leader(self, actor: str) -> None:
        if actor != self.team.leader:
            raise WorkPermissionError(f"只有团队 Leader {self.team.leader} 可以创建任务")

    def _require_task_leader(self, actor: str, task: Task) -> None:
        if actor != task.leader:
            raise WorkPermissionError(f"只有任务 Leader {task.leader} 可以执行该动作")

    def _require_assignee(self, actor: str, task: Task) -> None:
        if actor != task.assignee:
            raise WorkPermissionError(
                f"{task.id} 的当前执行者是 {task.assignee or '尚未分派'}，不是 {actor}"
            )

    def _require_known_member(self, member: str) -> None:
        if member not in self.member_ids:
            choices = ", ".join(sorted(self.member_ids))
            raise WorkValidationError(
                f"{member!r} 不在团队 {self.team.id} 中；可用成员: {choices}"
            )

    @staticmethod
    def _require_status(task: Task, allowed: Iterable[TaskStatus], action: str) -> None:
        states = frozenset(allowed)
        if task.status not in states:
            expected = ", ".join(str(state) for state in sorted(states, key=str))
            raise WorkTransitionError(
                f"{task.id} 当前为 {task.status}，不能{action}；允许状态: {expected}"
            )

    @staticmethod
    def _require_not_final(task: Task, action: str) -> None:
        if task.status in FINAL_STATES:
            raise WorkTransitionError(f"{task.id} 已进入 {task.status}，不能{action}")
