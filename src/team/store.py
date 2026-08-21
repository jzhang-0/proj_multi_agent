"""`~/.amux/teams/` 的团队档案存储。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from team.model import Team, TeamValidationError, team_from_dict, validate_team_id
from workspace.errors import WorkspaceError
from workspace.paths import TEAMS_DIR, amux_home

DEFAULT_TEAM_ID = "fable-core"


class TeamNotFound(WorkspaceError):
    """请求的团队不存在。"""


class TeamStore:
    """团队档案只在 AMUX_HOME 下保存，不写入用户项目。"""

    def __init__(self, home: str | Path | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home is not None else amux_home()

    @property
    def teams_dir(self) -> Path:
        return self.home / TEAMS_DIR

    def path_for(self, team_id: str) -> Path:
        return self.teams_dir / f"{validate_team_id(team_id)}.toml"

    def list(self) -> list[Team]:
        if not self.teams_dir.is_dir():
            return []
        return sorted(
            (self.load(path.stem) for path in self.teams_dir.glob("*.toml")),
            key=lambda team: team.id,
        )

    def load(self, team_id: str) -> Team:
        target = self.path_for(team_id)
        if not target.is_file():
            raise TeamNotFound(f"没有叫 {team_id!r} 的团队。先运行 amux team init")
        try:
            raw = tomllib.loads(target.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise TeamValidationError(f"无法解析 {target}: {exc}") from exc
        team = team_from_dict(raw, source=str(target))
        if team.id != team_id:
            raise TeamValidationError(
                f"{target} 内的 id {team.id!r} 必须与文件名 {team_id!r} 一致"
            )
        return team

    def init_default(self, *, force: bool = False) -> Path:
        """显式写入人类拍板的 Fable 默认团队。"""
        target = self.path_for(DEFAULT_TEAM_ID)
        if target.exists() and not force:
            raise WorkspaceError(f"默认团队已存在: {target}。如需重写请加 --force")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_DEFAULT_TEAM, encoding="utf-8")
        return target


_DEFAULT_TEAM = '''# 默认协作团队。command/args 是已在本机 CLI 上验证过的启动适配。
# Leader 直接对 human 负责；成员提交证据与评审意见；Leader 可在必要时接管实现。
id = "fable-core"
name = "Fable 协作组"
leader = "fable"
description = "以 Fable 为责任主体的默认 AI 协作团队"

[[members]]
id = "fable"
role = "leader"
model = "Claude Fable 5"
effort = "high"
speed = "standard"
responsibility = "面向 human，拆解、分派、验收；必要时接管实现并承担最终责任"
command = "claude"
args = ["--model", "fable", "--effort", "high", "--permission-mode", "acceptEdits"]

[[members]]
id = "sonnet"
role = "member"
model = "Sonnet"
effort = "xhigh"
speed = "standard"
responsibility = "实现与深入分析，提交可复核的证据"
command = "claude"
args = ["--model", "sonnet", "--effort", "xhigh", "--permission-mode", "acceptEdits"]

[[members]]
id = "opus"
role = "member"
model = "Opus"
effort = "high"
speed = "standard"
responsibility = "复杂问题分析、方案评审与风险检查"
command = "claude"
args = ["--model", "opus", "--effort", "high", "--permission-mode", "acceptEdits"]

[[members]]
id = "luna"
role = "member"
model = "Luna"
effort = "high"
speed = "fast"
responsibility = "快速调研、分流、验证与反馈"
command = "codex"
args = [
  "-m", "gpt-5.6-luna",
  "-c", "model_reasoning_effort=\\\"high\\\"",
  "-c", "service_tier=\\\"priority\\\"",
  "-s", "workspace-write", "-a", "on-request",
]

[[members]]
id = "sol"
role = "member"
model = "Sol"
effort = "xhigh"
speed = "standard"
responsibility = "独立实现、交叉验证与高难任务支持"
command = "codex"
args = [
  "-m", "gpt-5.6-sol",
  "-c", "model_reasoning_effort=\\\"xhigh\\\"",
  "-c", "service_tier=\\\"default\\\"",
  "-s", "workspace-write", "-a", "on-request",
]
'''
