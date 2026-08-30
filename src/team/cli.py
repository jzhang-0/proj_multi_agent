"""`amux team init|list|show|use|current|activate|add-member`。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from team.activation import activate_team
from team.binding import bind_team, load_team_binding
from team.model import Team, TeamValidationError
from team.store import DEFAULT_TEAM_ID, TeamStore
from workspace.errors import WorkspaceError
from workspace.resolve import ensure_from_cwd
from workspace.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amux team",
        description="管理保存在 ~/.amux/teams/ 的协作团队，并绑定到工作区。",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="写入默认 Fable 协作团队")
    init.add_argument("--force", action="store_true", help="重写已有默认团队")
    sub.add_parser("list", help="列出已保存团队")
    show = sub.add_parser("show", help="展示一个团队的 Leader、成员与职责")
    show.add_argument("team_id", help="团队 ID")
    use = sub.add_parser("use", help="将一个团队绑定到当前工作区")
    use.add_argument("team_id", help="团队 ID")
    sub.add_parser("current", help="显示当前工作区绑定的团队")
    activate = sub.add_parser("activate", help="绑定团队并替换当前工作区的运行成员")
    activate.add_argument("team_id", nargs="?", help="团队 ID，省略时使用当前绑定")
    add_member = sub.add_parser("add-member", help="向已保存团队档案追加成员")
    add_member.add_argument("team_id", help="团队 ID")
    add_member.add_argument("member_id", help="新成员 ID")
    add_member.add_argument("--model", required=True, help="成员模型名")
    add_member.add_argument("--responsibility", required=True, help="成员职责")
    add_member.add_argument(
        "--command", help="成员启动命令；使用 --preset 时可省略"
    )
    add_member.add_argument("--role", choices=("member", "leader"), default="member")
    add_member.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), default="high")
    add_member.add_argument("--speed", choices=("standard", "fast"), default="standard")
    add_member.add_argument("--arg", action="append", default=[], help="启动参数，可重复")
    add_member.add_argument("--env", action="append", default=[], help="KEY=VALUE，可重复")
    add_member.add_argument("--preset", choices=("claude", "codex"), help="套用默认启动适配")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    teams: TeamStore | None = None,
    store: Store | None = None,
    cwd: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    tmux=None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    team_store = teams or TeamStore()
    workspace_store = store or Store.default()
    here = cwd or Path.cwd()
    try:
        if args.action == "init":
            target = team_store.init_default(force=args.force)
            print(f"已写入默认团队 {DEFAULT_TEAM_ID}: {target}", file=output)
            return 0
        if args.action == "list":
            return _list(team_store, output)
        if args.action == "show":
            _show(team_store.load(args.team_id), output)
            return 0
        if args.action == "add-member":
            env = _parse_env(args.env)
            if args.command is None and args.preset is None:
                raise TeamValidationError("必须提供 --command，或使用 --preset claude|codex")
            team = team_store.add_member(
                args.team_id,
                args.member_id,
                model=args.model,
                responsibility=args.responsibility,
                command=args.command,
                role=args.role,
                effort=args.effort,
                speed=args.speed,
                args=args.arg,
                env=env,
                preset=args.preset,
            )
            added = team.members[-1]
            launch = " ".join((added.command or "", *added.args)).strip()
            print(
                f"已向团队 {team.id} 添加成员 {added.id}: "
                f"{added.model} · {added.role} · {added.effort} · {added.speed}",
                file=output,
            )
            print(f"启动适配: {launch}", file=output)
            print(f"需要运行 amux team activate {team.id} 才会拉起该成员。", file=output)
            return 0
        workspace = ensure_from_cwd(here, store=workspace_store)
        if args.action == "use":
            binding = bind_team(workspace, args.team_id, teams=team_store)
            print(f"工作区 {workspace.slug} 已绑定团队 {binding.team_id}", file=output)
            return 0
        if args.action == "current":
            binding = load_team_binding(workspace)
            if binding is None:
                print(f"工作区 {workspace.slug} 尚未选择团队。", file=output)
                return 0
            team = team_store.load(binding.team_id)
            print(
                f"工作区 {workspace.slug} 使用团队 {team.id} ({team.name})，"
                f"Leader: {team.leader_member.model}",
                file=output,
            )
            return 0
        if args.action == "activate":
            team_id = args.team_id
            if team_id is None:
                binding = load_team_binding(workspace)
                if binding is None:
                    raise WorkspaceError("当前工作区尚未选择团队。用 amux team activate <团队 ID>")
                team_id = binding.team_id
            activation = activate_team(
                workspace,
                team_id,
                teams=team_store,
                tmux=tmux,
            )
            print(f"工作区 {workspace.slug} 已激活团队 {activation.team.id}", file=output)
            for result in (*activation.stopped, *activation.started):
                print(result.line(), file=output)
            return 0
    except (WorkspaceError, OSError) as exc:
        print(f"[team] {exc}", file=errors)
        return 1
    print(
        "用法: amux team init|list|show|use|current|activate|add-member",
        file=errors,
    )
    return 2


def _parse_env(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key.strip():
            raise TeamValidationError(f"--env 必须是 KEY=VALUE: {value!r}")
        result[key.strip()] = item
    return result


def _list(teams: TeamStore, output: TextIO) -> int:
    saved = teams.list()
    if not saved:
        print("还没有团队。用 amux team init 写入默认团队。", file=output)
        return 0
    width = max(len(team.id) for team in saved)
    print(f"{'ID':<{width}}  Leader                 成员数  名称", file=output)
    for team in saved:
        print(
            f"{team.id:<{width}}  {team.leader_member.model:<21}  "
            f"{len(team.members) - 1:>3}  {team.name}",
            file=output,
        )
    return 0


def _show(team: Team, output: TextIO) -> None:
    print(f"团队: {team.name} ({team.id})", file=output)
    if team.description:
        print(team.description, file=output)
    print("", file=output)
    for member in team.members:
        prefix = "Leader" if member.role == "leader" else "成员"
        print(
            f"{prefix} {member.id}: {member.model} · {member.effort} · {member.speed}",
            file=output,
        )
        print(f"  职责: {member.responsibility}", file=output)
        if member.command is not None:
            print(f"  启动: {member.command} {' '.join(member.args)}", file=output)
