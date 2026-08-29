"""`/` 命令面板。

输入框里以 `/` 开头的一行是命令,不是发言。命令只做**控制**(谁在跑、
收编谁、静音谁),不发消息;发消息永远走普通输入。

错误提示的原则:说清哪儿不对 + 给出最接近的命令,不要只回一句"未知命令"。
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from console.layout import display_width, pad


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的声明。"""

    name: str
    arg: str
    help: str

    @property
    def usage(self) -> str:
        return f"/{self.name} {self.arg}".strip()


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("up", "<名字>", "拉起成员(已在跑的不动它)"),
    CommandSpec("down", "<名字>", "关掉成员的 tmux 会话"),
    CommandSpec("restart", "<名字>", "关掉再拉起"),
    CommandSpec("adopt", "<会话>", "把名册外的 tmux 会话收编为临时成员"),
    CommandSpec("mute", "<名字>", "临时拒收该成员的消息,再来一次取消"),
    CommandSpec("workspace", "<名字>", "切换绑定到该工作区"),
    CommandSpec("member", "add|rm <名字>", "增减本工作区成员"),
    CommandSpec("task", "[任务ID]", "打开任务看板或指定任务详情"),
    CommandSpec("help", "", "列出全部命令"),
)

COMMAND_NAMES = tuple(spec.name for spec in COMMANDS)

BY_NAME = {spec.name: spec for spec in COMMANDS}


def is_command(line: str) -> bool:
    return line.lstrip().startswith("/")


def parse_command(line: str) -> tuple[str, list[str]]:
    """`/up claude` → `("up", ["claude"])`。"""
    parts = line.lstrip().lstrip("/").split()
    if not parts:
        return "", []
    return parts[0], parts[1:]


def matching_commands(prefix: str) -> tuple[str, ...]:
    lowered = prefix.lower()
    return tuple(name for name in COMMAND_NAMES if name.startswith(lowered))


def did_you_mean(name: str) -> str:
    close = difflib.get_close_matches(name, COMMAND_NAMES, n=1, cutoff=0.5)
    return f",你是不是想用 /{close[0]}" if close else ",`/help` 看全部命令"


def help_lines() -> list[str]:
    width = max(display_width(spec.usage) for spec in COMMANDS)
    return [f"{pad(spec.usage, width)}  {spec.help}" for spec in COMMANDS]


class CommandRunner:
    """把命令翻译成对 roster / tmux 的调用,回一组要打到时间线上的行。

    依赖都可以是 None(没有 tmux、名册读不出来时),这时给出明确的失败原因
    而不是抛异常炸掉界面。
    """

    def __init__(
        self,
        *,
        lifecycle: object | None = None,
        adopter: object | None = None,
        muted: set[str] | None = None,
        on_members_changed: Callable[[], None] | None = None,
        switch_workspace: Callable[[str], list[str]] | None = None,
        add_member: Callable[[str], list[str]] | None = None,
        remove_member: Callable[[str], list[str]] | None = None,
        list_members: Callable[[], list[str]] | None = None,
        on_roster_changed: Callable[[], None] | None = None,
        open_task: Callable[[str | None], list[str]] | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.adopter = adopter
        self.muted: set[str] = muted if muted is not None else set()
        self.on_members_changed = on_members_changed
        self.on_roster_changed = on_roster_changed
        self.switch_workspace = switch_workspace
        self.add_member = add_member
        self.remove_member = remove_member
        self.list_members = list_members
        self.open_task = open_task

    def run(self, line: str) -> list[str]:
        name, args = parse_command(line)
        if not name:
            return ["命令不能为空,`/help` 看全部命令"]
        spec = BY_NAME.get(name)
        if spec is None:
            return [f"未知命令 /{name}{did_you_mean(name)}"]
        if spec.arg.startswith("<") and not args:
            return [f"用法:{spec.usage} —— {spec.help}"]
        if not spec.arg and args:
            return [f"/{name} 不接参数"]

        handler = getattr(self, f"_do_{name}")
        try:
            return handler(*args) if args else handler()
        except Exception as exc:  # roster/tmux 的失败都在这里变成一行提示
            return [f"/{name} 失败:{exc}"]

    # --- 各命令 ---------------------------------------------------------

    def _lifecycle_action(self, action: str, name: str) -> list[str]:
        if self.lifecycle is None:
            return [f"/{action} 不可用:没接上 tmux 或名册"]
        results = getattr(self.lifecycle, action)(name)
        return [result.line() for result in results]

    def _do_up(self, name: str, *extra: str) -> list[str]:
        return self._lifecycle_action("up", name)

    def _do_down(self, name: str, *extra: str) -> list[str]:
        return self._lifecycle_action("down", name)

    def _do_restart(self, name: str, *extra: str) -> list[str]:
        return self._lifecycle_action("restart", name)

    def _do_adopt(self, name: str, *extra: str) -> list[str]:
        if self.adopter is None:
            return ["/adopt 不可用:没接上 tmux 或名册"]
        adopted = self.adopter.adopt(name)
        if self.on_members_changed is not None:
            self.on_members_changed()
        return [f"[adopt] {adopted.name} 已收编为临时成员(重启后不保留)"]

    def _do_mute(self, name: str, *extra: str) -> list[str]:
        if name in self.muted:
            self.muted.discard(name)
            return [f"[mute] {name} 取消静音,消息恢复投递"]
        self.muted.add(name)
        return [f"[mute] {name} 已静音,它发出的消息会被拒收(再 /mute {name} 取消)"]

    def _do_workspace(self, name: str, *extra: str) -> list[str]:
        if self.switch_workspace is None:
            return ["/workspace 不可用:没接上工作区登记"]
        return self.switch_workspace(name)

    def _do_member(self, action: str, name: str = "", *extra: str) -> list[str]:
        if action == "list":
            if name:
                return ["用法:/member list"]
            if self.list_members is None:
                return ["/member 不可用:没接上工作区"]
            return self.list_members()
        if action in ("add", "rm"):
            if not name:
                return [f"用法:/member {action} <名字>"]
            callback = self.add_member if action == "add" else self.remove_member
            if callback is None:
                return ["/member 不可用:没接上工作区"]
            lines = callback(name)
            if self.on_roster_changed is not None:
                self.on_roster_changed()
            elif self.on_members_changed is not None:
                self.on_members_changed()
            return lines
        return ["用法:/member add|rm <名字> 或 /member list"]

    def _do_task(self, task_id: str = "", *extra: str) -> list[str]:
        if extra:
            return ["用法:/task [任务ID]"]
        if self.open_task is None:
            return ["/task 不可用:当前工作区没有绑定团队"]
        return self.open_task(task_id or None)

    def _do_help(self, *extra: str) -> list[str]:
        return ["可用命令:", *help_lines()]


def muted_names(runner: CommandRunner) -> Sequence[str]:
    return sorted(runner.muted)
