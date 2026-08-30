"""每次请求解析一次的工作区/团队/总线/tmux 上下文；只读、无副作用。

不调用 `ensure_from_cwd`(会注册新工作区)、不调用会改 tmux 窗口尺寸或按键
的任何方法——快照端点只看，不改(架构 §1 规则 2)。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from bus import BusPaths
from control.members import member_names
from team.binding import load_team_binding
from team.store import TeamStore
from tmuxctl import Tmux
from tmuxctl.errors import TmuxError
from web.errors import ApiError
from workspace.model import Workspace
from workspace.resolve import resolve_from_cwd
from workspace.session import NamespacedTmux, SessionNames, bind_tmux


@dataclass(frozen=True)
class SnapshotContext:
    """一次请求内共用的只读依赖；`workspace is None` 表示 cwd 未登记。"""

    workspace: Workspace | None
    paths: BusPaths | None
    tmux: Tmux | NamespacedTmux | None
    names: tuple[str, ...]


def build_context(cwd: Path | None = None) -> SnapshotContext:
    here = cwd if cwd is not None else Path.cwd()
    workspace = resolve_from_cwd(here)
    if workspace is None:
        return SnapshotContext(workspace=None, paths=None, tmux=None, names=())
    paths = BusPaths.for_workspace(workspace).ensure()
    tmux: Tmux | NamespacedTmux | None = None
    with contextlib.suppress(TmuxError):
        tmux = bind_tmux(names=SessionNames(slug=workspace.slug))
    names = member_names(cwd=workspace.project_root)
    return SnapshotContext(workspace=workspace, paths=paths, tmux=tmux, names=names)


def require_workspace(ctx: SnapshotContext) -> Workspace:
    if ctx.workspace is None:
        raise ApiError(
            "workspace-unregistered",
            "当前目录不属于任何已登记工作区",
            status_code=409,
        )
    return ctx.workspace


def require_paths(ctx: SnapshotContext) -> BusPaths:
    if ctx.paths is None:
        raise ApiError(
            "workspace-unregistered",
            "当前目录不属于任何已登记工作区",
            status_code=409,
        )
    return ctx.paths


def team_home(workspace: Workspace) -> Path:
    """与 `WorkService.for_workspace` 同一算法：state_dir 的祖父目录是 AMUX_HOME。"""
    return workspace.state_dir.parent.parent


def load_bound_team(workspace: Workspace):
    """返回 `(binding, team)`；未绑定时 `(None, None)`，不抛错(对齐 /team 的 bound:false 语义)。"""
    binding = load_team_binding(workspace)
    if binding is None:
        return None, None
    team = TeamStore(team_home(workspace)).load(binding.team_id)
    return binding, team
