"""总控台 TUI:三栏布局与应用骨架。

布局(产品定义里的那张图):

```
┌─成员─────┬─群聊时间线──────────┬─成员详情(可折叠)─┐
│ claude   │ 12:01 human→claude  │ [选中成员的画面]  │
├──────────┴─────────────────────┴───────────────────┤
│ > @claude 修一下登录页的bug_                       │
└────────────────────────────────────────────────────┘
```

三条规则:详情栏默认折叠,选中成员才展开;终端宽度不够(< 100 列)时
详情栏自动让位,把宽度还给时间线;最小可用尺寸 80×24,成员栏和时间线
在这个尺寸下都还完整可读。

退出路径只做两件事:停投递循环、关应用。**绝不动 tmux**——成员会话是
成员自己的,总控台退出不该带走任何一个。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, ListItem, ListView, Static

from bus import BusPaths, DeliveryOutcome, DeliveryResult, Message, deposit
from bus.audit import AuditLog
from bus.hub import tmux_deliver
from console.buspump import BusPump, MutePolicy
from console.commands import CommandRunner, is_command
from console.compose import ComposeInput, split_address
from console.control import ConfirmControlScreen, ControlFeedback, MemberController
from console.members import MemberStatusService, member_names, pending_counts
from console.mirror import Mirror
from console.timeline import TimelineEntry, history
from console.widgets import MemberCard, Timeline
from roster import RosterError, load_roster
from roster.lifecycle import Lifecycle
from tmuxctl import Tmux, TmuxError

#: 低于这个列数就不再显示详情栏:80 列时成员栏 + 时间线已经占满,
#: 再挤一栏会把时间线压到没法读
DETAIL_MIN_WIDTH = 100

#: 最小可用尺寸(产品定义)
MIN_SIZE = (80, 24)

#: 详情栏画面刷新间隔(秒)。产品定义:活跃窗格 < 100ms
MIRROR_INTERVAL = 0.1


class ConsoleApp(App[None]):
    """一台机器上多个 AI CLI 的群聊与指挥中心。"""

    TITLE = "总控台"
    SUB_TITLE = "本机 AI 群聊与指挥中心"

    CSS = """
    #body {
        height: 1fr;
    }
    #members {
        width: 18;
        border-right: solid $primary-darken-2;
        background: $surface;
    }
    #members > ListItem {
        height: 2;
        padding: 0 1;
    }
    .member-card {
        height: 2;
    }
    #center {
        width: 1fr;
    }
    #timeline {
        height: 1fr;
        padding: 0 1;
    }
    #compose {
        height: 3;
        border: tall $primary-darken-2;
    }
    #suggestions {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        display: none;
    }
    #detail {
        width: 34;
        border-left: solid $primary-darken-2;
        padding: 0 1;
        display: none;
        overflow-x: hidden;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", priority=True, show=False),
        Binding("escape", "clear_selection", "收起详情"),
        Binding("pageup", "timeline_scroll('page_up')", "上翻", show=False),
        Binding("pagedown", "timeline_scroll('page_down')", "下翻", show=False),
        Binding("home", "timeline_scroll('home')", "回到最早", show=False),
        Binding("end", "timeline_scroll('end')", "回到最新", show=False),
        Binding("f5", "interrupt_selected", "打断"),
        Binding("f6", "terminate_selected", "终止"),
        Binding("f7", "restart_selected", "重启"),
        Binding("f8", "takeover_selected", "接管"),
    ]

    def __init__(
        self,
        paths: BusPaths | None = None,
        *,
        deliver: Callable[[Message], bool] = tmux_deliver,
        members: tuple[str, ...] | None = None,
        snapshotter: object | None = None,
        member_status: MemberStatusService | None = None,
        pump_enabled: bool = True,
        controller: MemberController | None = None,
    ) -> None:
        super().__init__()
        self.paths = (paths or BusPaths.resolve()).ensure()
        #: 被 /mute 静音的成员;策略在投递前据此拒收
        self.muted: set[str] = set()
        self.pump = BusPump(
            self.paths, self._on_result, deliver=deliver, policy=MutePolicy(self.muted)
        )
        self.pump_enabled = pump_enabled
        self.members: tuple[str, ...] = members if members is not None else member_names()
        if member_status is None:
            tmux = None
            if deliver is tmux_deliver:
                with contextlib.suppress(TmuxError):
                    tmux = Tmux()
            member_status = MemberStatusService(self.members, tmux)
            if deliver is tmux_deliver and tmux is None:
                for name in self.members:
                    member_status.set_alive(name, False)
        self.member_status = member_status
        if controller is None and deliver is tmux_deliver and member_status.tmux is not None:
            try:
                roster = load_roster()
                controller = MemberController(
                    member_status.tmux,
                    Lifecycle(roster, member_status.tmux),
                    AuditLog(self.paths),
                )
            except (OSError, RosterError, TmuxError):
                controller = None
        self.controller = controller
        self.selected_member: str | None = None
        #: 上一个对话对象:不写 @ 前缀时默认发给它
        self.last_target: str | None = None
        #: 详情栏画面来源(TMX-004);None 表示还没接上 tmux
        self.snapshotter = snapshotter
        self._mirror_timer = None
        self.commands = CommandRunner(muted=self.muted, on_members_changed=self._schedule_reload)

    # --- 布局 -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            # initial_index=None:一起来不要自动高亮第一个成员,
            # 否则"详情栏默认折叠"就被 ListView 自己破坏了
            yield ListView(
                *(
                    ListItem(
                        MemberCard(
                            self.member_status.snapshot(name),
                            id=f"card-{name}",
                            classes="member-card",
                        ),
                        id=f"member-{name}",
                    )
                    for name in self.members
                ),
                id="members",
                initial_index=None,
            )
            with Vertical(id="center"):
                yield Timeline(id="timeline")
                yield Static("", id="suggestions", markup=False)
                yield ComposeInput(
                    members=self.members,
                    placeholder="@名字 说点什么,回车发送",
                    id="compose",
                )
            yield Mirror(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        timeline = self.query_one("#timeline", Timeline)
        timeline.note(f"[总控台] 总线目录 {self.paths.root}")
        timeline.backfill(history(AuditLog(self.paths)))
        timeline.note("[总控台] ↑↓ 选成员,PgUp/PgDn 翻时间线,Esc 收起详情;q 或 Ctrl-C 退出")
        # 焦点先给成员栏:CON-002 的交互就是选成员。输入框的焦点规则在 CON-004/012。
        self.query_one("#members", ListView).focus()
        self.refresh_member_cards()
        self.set_interval(0.5, self.refresh_member_cards)
        if self.member_status.can_monitor:
            self.run_worker(self.member_status.run(), group="member-status", exclusive=True)
        if self.pump_enabled:
            self.pump.start()
        self._start_mirror()
        self._connect_roster()

    def on_unmount(self) -> None:
        self.member_status.stop()
        if self.pump_enabled:
            self.pump.stop()
        if self._mirror_timer is not None:
            self._mirror_timer.stop()
            self._mirror_timer = None

    def refresh_member_cards(self) -> None:
        """每 0.5 秒刷新，保证状态变化 1 秒内出现在界面。"""
        counts = pending_counts(self.paths)
        # 定时器与卸载或 `/adopt` 重建列表可能擦肩；只刷新仍挂在 DOM 上的卡片。
        for card in self.query(MemberCard):
            name = card.snapshot.name
            card.apply(self.member_status.snapshot(name, queued=counts[name]))

    # --- 命令面板要用的名册/生命周期 ---------------------------------------

    def _connect_roster(self) -> None:
        """接上 roster 的生命周期与收编能力;接不上就让命令给出明确失败。"""
        try:
            from roster.adopt import SessionAdopter
            from roster.lifecycle import Lifecycle
            from roster.load import load_roster
            from tmuxctl import Tmux

            roster, tmux = load_roster(), Tmux()
            self.commands.lifecycle = Lifecycle(roster, tmux)
            self.commands.adopter = SessionAdopter(roster, tmux)
        except Exception as exc:
            self.query_one("#timeline", Timeline).note(f"[总控台] 控制命令不可用:{exc}")

    def _schedule_reload(self) -> None:
        """命令处理是同步的,重建成员栏要等组件真的移除,所以排到下一轮。"""
        self.call_later(self._reload_members)

    async def _reload_members(self) -> None:
        """名册变了(`/adopt`)之后重建成员栏和补全候选。

        `clear()` 是异步的:不等它做完就 append,会撞上"同 ID 已存在"并把
        成员栏清空(实测踩过)。
        """
        adopter = self.commands.adopter
        if adopter is None:
            return
        self.members = tuple(adopter.member_names())
        self.member_status.track(self.members)
        listing = self.query_one("#members", ListView)
        await listing.clear()
        for name in self.members:
            await listing.append(
                ListItem(
                    MemberCard(
                        self.member_status.snapshot(name),
                        id=f"card-{name}",
                        classes="member-card",
                    ),
                    id=f"member-{name}",
                )
            )
        self.query_one("#compose", ComposeInput).members = self.members

    # --- 成员详情:终端画面镜像 -------------------------------------------

    def _start_mirror(self) -> None:
        """接上快照器并起刷新定时器(默认先暂停,选中成员才跑)。"""
        if self.snapshotter is None:
            try:
                from tmuxctl import PaneSnapshotter, Tmux

                self.snapshotter = PaneSnapshotter(Tmux())
            except Exception as exc:  # tmux 不在或版本不够:详情栏降级成提示
                self.query_one("#timeline", Timeline).note(f"[总控台] 详情栏不可用:{exc}")
                return
        self._mirror_timer = self.set_interval(MIRROR_INTERVAL, self._refresh_mirror, pause=True)

    def _mirror(self) -> Mirror | None:
        """详情栏组件;应用正在拆的时候可能已经没有了。"""
        found = self.query("#detail")
        return found.only_one(Mirror) if found else None

    async def _refresh_mirror(self) -> None:
        """拉一帧成员画面。只有详情栏真的在显示时才会被调到。"""
        member, mirror = self.selected_member, self._mirror()
        if member is None or mirror is None or self.snapshotter is None or not mirror.display:
            return
        try:
            snapshot = await self.snapshotter.capture(
                member, color=True, start=mirror.capture_start
            )
        except Exception as exc:
            mirror.notice(f"取不到 {member} 的画面:{exc}")
            return
        mirror.show_screen(snapshot.text)

    # --- 详情栏的展开/让位 ------------------------------------------------

    @property
    def detail_visible(self) -> bool:
        mirror = self._mirror()
        return bool(mirror is not None and mirror.display)

    def _sync_detail(self, width: int | None = None) -> None:
        """详情栏只在"选中了成员"且"宽度够"时出现,两个条件缺一不可。

        `width` 显式传进来是因为处理 Resize 事件时 `self.size` 还是旧值,
        必须用事件里带的新尺寸判断。
        """
        detail = self._mirror()
        if detail is None:
            return
        wide_enough = (self.size.width if width is None else width) >= DETAIL_MIN_WIDTH
        detail.display = self.selected_member is not None and wide_enough
        # 不可见就把定时器停掉:看不见的画面不值得每 100ms 去问一次 tmux
        if self._mirror_timer is not None:
            self._mirror_timer.resume() if detail.display else self._mirror_timer.pause()
        if detail.display:
            detail.notice(f"成员详情 · {self.selected_member}(取画面中…)")

    def select_member(self, name: str | None) -> None:
        self.selected_member = name
        mirror = self._mirror()
        if mirror is not None:
            mirror.history_offset = 0  # 换人就回到当前画面
        self._sync_detail()

    def action_clear_selection(self) -> None:
        self.select_member(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None or item.id is None:
            return
        self.select_member(item.id.removeprefix("member-"))

    def on_resize(self, event: events.Resize) -> None:
        self._sync_detail(width=event.size.width)

    # --- 输入框 ----------------------------------------------------------

    def on_input_changed(self, event: ComposeInput.Changed) -> None:
        if event.input.id == "compose":
            self.query_one("#compose", ComposeInput).refresh_candidates()

    def on_compose_input_candidates_changed(self, event: ComposeInput.CandidatesChanged) -> None:
        self._sync_suggestions()

    def on_input_submitted(self, event: ComposeInput.Submitted) -> None:
        if event.input.id == "compose":
            self.send_from_input(event.value)

    def _sync_suggestions(self) -> None:
        """候选行:只在有候选时占一行,当前候选加方括号。"""
        compose = self.query_one("#compose", ComposeInput)
        row = self.query_one("#suggestions", Static)
        row.display = bool(compose.candidates)
        if not compose.candidates:
            return
        current = compose.current_candidate
        mark = "/" if compose.candidate_kind == "command" else ""
        shown = " ".join(
            f"[{mark}{name}]" if name == current else f"{mark}{name}"
            for name in compose.candidates
        )
        row.update(f"Tab/↑↓ 选择:{shown}")

    def _update_placeholder(self) -> None:
        compose = self.query_one("#compose", ComposeInput)
        if self.last_target is None:
            compose.placeholder = "@名字 说点什么,回车发送"
        else:
            compose.placeholder = f"回车发给 {self.last_target}(@名字 可改收件人)"

    def run_command(self, raw: str) -> None:
        """执行一条 `/` 命令,输出打到时间线上。"""
        compose = self.query_one("#compose", ComposeInput)
        timeline = self.query_one("#timeline", Timeline)
        timeline.note(f"› {raw.strip()}")
        for line in self.commands.run(raw):
            timeline.note(f"  {line}")
        compose.remember(raw)
        compose.value = ""
        compose.candidates = ()
        self._sync_suggestions()

    def send_from_input(self, raw: str) -> None:
        """把输入框里的一行发上总线;`/` 开头的当命令执行。发件人永远是 human。"""
        compose = self.query_one("#compose", ComposeInput)
        timeline = self.query_one("#timeline", Timeline)
        if is_command(raw):
            self.run_command(raw)
            return
        addressed, text = split_address(raw)
        target = addressed or self.last_target
        if not text:
            return
        if target is None:
            timeline.note("[总控台] 还没有对话对象,先用 @名字 指定收件人")
            return
        deposit(Message.create(target, text, sender="human"), self.paths)
        compose.remember(raw)
        compose.value = ""
        compose.candidates = ()
        self._sync_suggestions()
        self.last_target = target
        self._update_placeholder()

    # --- 总线流量 --------------------------------------------------------

    def _on_result(self, result: DeliveryResult) -> None:
        """投递线程回调 → 交回 UI 线程渲染。"""
        self.call_from_thread(self.show_result, result)

    def show_result(self, result: DeliveryResult) -> None:
        """把一条投递结果追加到时间线。"""
        if (
            result.outcome is DeliveryOutcome.DELIVERED
            and result.message is not None
            and result.message.to in self.members
        ):
            self.member_status.mark_working(result.message.to)
        self.query_one("#timeline", Timeline).add(TimelineEntry.from_result(result))

    # --- 滚动回看 --------------------------------------------------------

    def action_timeline_scroll(self, direction: str) -> None:
        timeline = self.query_one("#timeline", Timeline)
        {
            "page_up": timeline.scroll_page_up,
            "page_down": timeline.scroll_page_down,
            "home": timeline.scroll_home,
            "end": timeline.scroll_end,
        }[direction]()

    # --- 成员控制 --------------------------------------------------------

    def _control_target(self) -> str | None:
        if self.selected_member is None:
            self.query_one("#timeline", Timeline).note("[总控台] 先在成员栏选择一个成员")
            return None
        if self.controller is None:
            self.query_one("#timeline", Timeline).note("[总控台] 成员控制不可用")
            return None
        return self.selected_member

    def _show_control_feedback(self, feedback: ControlFeedback) -> None:
        mark = "✓" if feedback.changed else "·"
        self.query_one("#timeline", Timeline).note(
            f"[控制] {mark} {feedback.action} {feedback.target}: {feedback.detail}"
        )

    def _perform_control(self, action: str, target: str) -> None:
        assert self.controller is not None
        try:
            feedback = getattr(self.controller, action)(target)
        except Exception as exc:
            self.query_one("#timeline", Timeline).note(
                f"[控制] ✗ {action} {target}: {type(exc).__name__}: {exc}"
            )
            return
        self._show_control_feedback(feedback)

    def action_interrupt_selected(self) -> None:
        target = self._control_target()
        if target is not None:
            self._perform_control("interrupt", target)

    def _confirm_selected(self, action: str, label: str) -> None:
        target = self._control_target()
        if target is None:
            return

        def after_confirmation(confirmed: bool) -> None:
            if confirmed:
                self._perform_control(action, target)

        self.push_screen(ConfirmControlScreen(label, target), after_confirmation)

    def action_terminate_selected(self) -> None:
        self._confirm_selected("terminate", "终止")

    def action_restart_selected(self) -> None:
        self._confirm_selected("restart", "重启")

    def action_takeover_selected(self) -> None:
        target = self._control_target()
        if target is None:
            return
        assert self.controller is not None
        invoked = False
        try:
            with self.suspend():
                invoked = True
                feedback = self.controller.takeover(target)
        except Exception as exc:
            if not invoked:
                self.controller.record_failure("takeover", target, exc)
            self.query_one("#timeline", Timeline).note(
                f"[控制] ✗ takeover {target}: {type(exc).__name__}: {exc}"
            )
            return
        self._show_control_feedback(feedback)
