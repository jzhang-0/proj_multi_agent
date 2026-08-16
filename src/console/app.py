"""总控台 TUI:会话列表 + 一块主画面。

布局:

```
┌──────────┬─ 主画面 ─────────────────────────────────┐
│≡ 群聊    │ 12:01 human→claude: 写fizzbuzz           │
│ 未读 3   │ 12:03 claude→codex: 请review             │
│○ claude  │  …或者选中成员时,这里是它的终端画面      │
│○ codex   │                                          │
├──────────┴──────────────────────────────────────────┤
│ > @claude 修一下登录页的bug_                        │
└─────────────────────────────────────────────────────┘
```

左边是**会话列表**:第一项「群聊时间线」,后面每个成员一项——和聊天软件的
联系人列表一个意思。右边是**同一块主画面**,选中谁就显示谁:群聊显示时间线,
成员显示它的终端画面镜像。输入框在底部通栏。

两条规则:主画面同一时刻只有一个内容(看不见的那个不刷新、不抓 tmux);
在群聊之外的会话里,输入框直接把字**键入那个成员的终端**(要走群聊就用
`@名字` 开头),这样人不用 attach 出去就能和某个 AI 单独说话。

退出路径只做两件事:停投递循环、关应用。**绝不动 tmux**——成员会话是
成员自己的,总控台退出不该带走任何一个。
"""

from __future__ import annotations

import asyncio
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
from console.health import ConsoleHealthMonitor, FaultEvent, FaultKind
from console.help import ShortcutHelpScreen
from console.members import MemberStatusService, member_names, pending_counts
from console.mirror import Mirror
from console.theme import THEMES, Tokens
from console.theme import tokens as theme_tokens
from console.timeline import TimelineEntry, history
from console.widgets import ConversationCard, MemberCard, Timeline
from roster import RosterError, load_roster
from roster.lifecycle import Lifecycle
from tmuxctl import Tmux, TmuxError

#: 最小可用尺寸(产品定义)
MIN_SIZE = (80, 24)

#: 会话列表里群聊那一项的 ID;其余项是 `member-<名字>`
TIMELINE_ITEM_ID = "conv-timeline"

#: 小于这个尺寸就不去动成员窗口:把别人的终端压成一条缝比留白更糟
MIN_FIT_SIZE = (60, 15)

#: 详情栏画面刷新间隔(秒)。产品定义要求活跃窗格 **< 100ms**,所以定时器
#: 本身要压在 100ms 以内——正好取 100ms 的话,加上调度抖动实测 P95 会踩到
#: 104ms(CON-010 量出来的),留一点余量。
MIRROR_INTERVAL = 0.08


class ConsoleApp(App[None]):
    """一台机器上多个 AI CLI 的群聊与指挥中心。"""

    TITLE = "总控台"
    SUB_TITLE = "本机 AI 群聊与指挥中心"

    CSS = """
    #body {
        height: 1fr;
    }
    /* 会话列表只要放得下"○ IDLE  名字"和"排队0 · 未活动"两行,
       剩下的宽度全给主画面——成员终端画面才是吃宽度的那个 */
    #members {
        width: 22;
        border-right: solid $primary-darken-2;
        background: $surface;
    }
    #members > ListItem {
        height: 2;
        padding: 0 1;
    }
    /* 群聊那一项底下画一条线,和成员分开。选择器必须比 `#members > ListItem`
       更具体,否则 height 被那条规则压回 2,卡片第二行就被边框吃掉 */
    #members > #conv-timeline {
        height: 3;
        border-bottom: solid $primary-darken-2;
    }
    .member-card, .conversation-card {
        height: 2;
    }
    #stage {
        width: 1fr;
    }
    #timeline {
        height: 1fr;
        padding: 0 1;
    }
    #timeline:focus {
        background-tint: $accent 8%;
    }
    #compose {
        height: 3;
        border: tall $primary-darken-2;
    }
    #compose.-direct {
        border: tall $secondary;
    }
    #suggestions {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        display: none;
    }
    #detail {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        display: none;
        overflow-x: hidden;
    }
    #members:focus {
        background-tint: $accent 8%;
    }
    #compose:focus {
        border: tall $accent;
    }
    #detail:focus {
        background-tint: $accent 8%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", priority=True, show=False),
        Binding("question_mark", "show_shortcuts", "帮助"),
        Binding("f1", "show_shortcuts", "帮助", show=False),
        Binding("escape", "open_timeline", "回群聊"),
        Binding("f2", "open_timeline", "回群聊", show=False),
        Binding("t", "toggle_theme", "深浅主题"),
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
        health_monitor: ConsoleHealthMonitor | None = None,
        fit_windows: bool = True,
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
        if health_monitor is None and deliver is tmux_deliver:
            health_monitor = ConsoleHealthMonitor(
                self.paths,
                self.members,
                member_status.tmux,
            )
        self.health_monitor = health_monitor
        #: 当前会话:None 表示看的是群聊时间线,否则是那个成员
        self.selected_member: str | None = None
        #: 不在群聊会话里的这段时间过去了多少条流量(显示成未读数)
        self.unseen_traffic = 0
        #: 主画面上一次提示对应的成员;没变就别重写提示,否则每来一个
        #: Resize 都会把正在显示的画面刷成"取画面中…"
        self._detail_state: str | None = None
        #: 上一个对话对象:不写 @ 前缀时默认发给它
        self.last_target: str | None = None
        #: 详情栏画面来源(TMX-004);None 表示还没接上 tmux
        self.snapshotter = snapshotter
        self._mirror_timer = None
        #: 打开成员会话时把它的 tmux 窗口调成主画面大小(`--no-fit` 关掉)
        self.fit_windows = fit_windows
        #: 已经给谁调过多大,避免每帧都下发 resize-window
        self._fitted: dict[str, tuple[int, int]] = {}
        self.commands = CommandRunner(muted=self.muted, on_members_changed=self._schedule_reload)

    # --- 布局 -----------------------------------------------------------

    def _member_item(self, name: str) -> ListItem:
        return ListItem(
            MemberCard(
                self.member_status.snapshot(name),
                id=f"card-{name}",
                classes="member-card",
            ),
            id=f"member-{name}",
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            # initial_index=0:一起来停在群聊上,主画面直接是时间线
            yield ListView(
                ListItem(
                    ConversationCard(id="card-timeline", classes="conversation-card"),
                    id=TIMELINE_ITEM_ID,
                ),
                *(self._member_item(name) for name in self.members),
                id="members",
                initial_index=0,
            )
            with Vertical(id="stage"):
                yield Timeline(id="timeline")
                yield Mirror(id="detail")
        yield Static("", id="suggestions", markup=False)
        yield ComposeInput(
            members=self.members,
            placeholder="@名字 说点什么,回车发送",
            id="compose",
        )
        yield Footer()

    def _register_themes(self) -> None:
        """把 `console.theme` 的两套 token 注册成 Textual 主题。"""
        from textual.theme import Theme

        for token_set in THEMES.values():
            self.register_theme(
                Theme(
                    name=token_set.name,
                    primary=token_set.status["working"],
                    secondary=token_set.status["idle"],
                    accent=token_set.accent,
                    warning=token_set.status["stuck"],
                    error=token_set.status["dead"],
                    success=token_set.status["working"],
                    background=token_set.background,
                    surface=token_set.surface,
                    panel=token_set.panel,
                    foreground=token_set.foreground,
                    dark=token_set.dark,
                )
            )

    def action_toggle_theme(self) -> None:
        """深浅主题互换,已经画出来的内容按新 token 重画。"""
        from console.theme import toggle

        self.apply_theme(toggle())

    def apply_theme(self, token_set: Tokens) -> None:
        self.theme = token_set.name
        self.query_one("#timeline", Timeline).rerender()
        self.refresh_member_cards()

    def on_mount(self) -> None:
        self._register_themes()
        self.theme = theme_tokens().name
        timeline = self.query_one("#timeline", Timeline)
        timeline.note(f"[总控台] 总线目录 {self.paths.root}")
        timeline.backfill(history(AuditLog(self.paths)))
        timeline.note("[总控台] ↑↓ 选会话(群聊/成员),Esc 回群聊,PgUp/PgDn 翻页;q 或 Ctrl-C 退出")
        # 焦点先给会话列表:CON-002 的交互就是选会话。输入框的焦点规则在 CON-004/012。
        self.query_one("#members", ListView).focus()
        self.refresh_member_cards()
        self.set_interval(0.5, self.refresh_member_cards)
        if self.member_status.can_monitor:
            self.run_worker(self.member_status.run(), group="member-status", exclusive=True)
        if self.pump_enabled:
            self.pump.start()
        if self.health_monitor is not None:
            self.run_worker(
                self.health_monitor.run(self._on_fault_event),
                group="console-health",
                exclusive=True,
            )
        self._start_mirror()
        self._sync_stage()
        self._connect_roster()

    def on_unmount(self) -> None:
        self.member_status.stop()
        if self.health_monitor is not None:
            self.health_monitor.stop()
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
        self._sync_unread()

    def _sync_unread(self) -> None:
        """群聊那张卡上的未读数。"""
        for card in self.query(ConversationCard):
            card.apply(self.unseen_traffic, watching=self.selected_member is None)

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
        if self.health_monitor is not None:
            self.health_monitor.track(self.members)
        listing = self.query_one("#members", ListView)
        await listing.clear()
        await listing.append(
            ListItem(
                ConversationCard(id="card-timeline", classes="conversation-card"),
                id=TIMELINE_ITEM_ID,
            )
        )
        for name in self.members:
            await listing.append(self._member_item(name))
        self.query_one("#compose", ComposeInput).members = self.members
        self._sync_unread()

    # --- 成员详情:终端画面镜像 -------------------------------------------

    def _start_mirror(self) -> None:
        """接上快照器并起刷新定时器(默认先暂停,选中成员才跑)。"""
        if self.snapshotter is None:
            try:
                from tmuxctl import PaneSnapshotter, Tmux

                # 缓存窗口必须小于刷新间隔:两者相等时定时器每隔一拍就吃到
                # 缓存,实测画面更新间隔会翻倍到 200ms(CON-010 量出来的)
                self.snapshotter = PaneSnapshotter(Tmux(), min_interval=MIRROR_INTERVAL / 2)
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
        await self._fit_member_window(member, mirror.content_size)
        try:
            snapshot = await self.snapshotter.capture(
                member, color=True, start=mirror.capture_start
            )
        except Exception as exc:
            mirror.notice(f"取不到 {member} 的画面:{exc}")
            return
        mirror.show_screen(snapshot.text)

    async def _fit_member_window(self, member: str, size: object) -> None:
        """把成员的 tmux 窗口调成主画面这么大,让它自己重排填满。

        不调的话画面就是成员窗口原本那么大(实测 agy/cursor 都是 80×24),放在
        一块两百列的主画面里只占左上角一小块,剩下全是黑的。尺寸没变就不重复
        下发——`resize-window` 每次都是一次 tmux 进程启动。
        """
        tmux = self.member_status.tmux
        width, height = int(getattr(size, "width", 0)), int(getattr(size, "height", 0))
        if not self.fit_windows or tmux is None:
            return
        if width < MIN_FIT_SIZE[0] or height < MIN_FIT_SIZE[1]:
            return
        if self._fitted.get(member) == (width, height):
            return
        self._fitted[member] = (width, height)
        try:
            await asyncio.to_thread(tmux.fit_window, member, width, height)
        except Exception as exc:  # 会话没了/tmux 不认这条命令:留白也比崩了强
            self._fitted.pop(member, None)
            self._note(f"[总控台] 调不动 {member} 的窗口尺寸:{type(exc).__name__}: {exc}")

    def _release_member_window(self, member: str) -> None:
        """把窗口尺寸还给 tmux(F8 接管前必须还,否则 attach 上去尺寸对不上)。"""
        tmux = self.member_status.tmux
        self._fitted.pop(member, None)
        if not self.fit_windows or tmux is None:
            return
        with contextlib.suppress(Exception):
            tmux.release_window_size(member)

    # --- 主画面:群聊时间线 ⇄ 成员画面 --------------------------------------

    @property
    def detail_visible(self) -> bool:
        """主画面上现在是不是成员画面(而不是群聊时间线)。"""
        mirror = self._mirror()
        return bool(mirror is not None and mirror.display)

    def _sync_stage(self) -> None:
        """主画面同一时刻只放一个内容,看不见的那个不刷新也不抓 tmux。"""
        detail = self._mirror()
        if detail is None:
            return
        watching_member = self.selected_member is not None
        detail.display = watching_member
        self.query_one("#timeline", Timeline).display = not watching_member
        if self._mirror_timer is not None:
            self._mirror_timer.resume() if watching_member else self._mirror_timer.pause()
        if not watching_member:
            self.unseen_traffic = 0
        self._sync_unread()
        self._update_placeholder()
        if self.selected_member != self._detail_state:
            self._detail_state = self.selected_member
            if watching_member:
                detail.notice(f"成员详情 · {self.selected_member}(取画面中…)")

    def select_member(self, name: str | None) -> None:
        """切会话:`None` 表示回群聊时间线。左栏高亮跟着走。"""
        self.selected_member = name
        mirror = self._mirror()
        if mirror is not None:
            mirror.history_offset = 0  # 换人就回到当前画面
        self._sync_stage()
        self._highlight_conversation(name)

    def _highlight_conversation(self, name: str | None) -> None:
        """把左栏高亮挪到当前会话上(用代码切会话时也要同步)。"""
        listing = self.query("#members")
        if not listing:
            return
        view = listing.only_one(ListView)
        want = TIMELINE_ITEM_ID if name is None else f"member-{name}"
        for index, item in enumerate(view.children):
            if item.id == want:
                if view.index != index:
                    view.index = index
                return

    def action_open_timeline(self) -> None:
        self.select_member(None)

    def action_show_shortcuts(self) -> None:
        self.push_screen(ShortcutHelpScreen())

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None or item.id is None:
            return
        if item.id == TIMELINE_ITEM_ID:
            self.select_member(None)
            return
        self.select_member(item.id.removeprefix("member-"))

    def on_resize(self, event: events.Resize) -> None:
        self._sync_stage()

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
        # 在成员会话里,输入框直接连着那个成员的终端;边框也换个色提醒
        compose.set_class(self.selected_member is not None, "-direct")
        if self.selected_member is not None:
            # 提示里只用等宽字体一定画得出的字符:⌨ 这类符号在不少终端字体里
            # 是缺字形的小方块,画出来反而像界面坏了
            compose.placeholder = f"直连 {self.selected_member} 的终端,回车键入(@名字 走群聊)"
        elif self.last_target is None:
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

    def _accept_input(self, raw: str) -> None:
        """一行发出去之后的收尾:记进发言历史、清空、收起候选。"""
        compose = self.query_one("#compose", ComposeInput)
        compose.remember(raw)
        compose.value = ""
        compose.candidates = ()
        self._sync_suggestions()

    def type_into_member(self, member: str, raw: str, text: str) -> None:
        """把一行字直接键入成员终端——等于人在它自己的窗口里敲了这一行。

        不加 `[群消息] 来自 human:` 前缀:这是人在跟这个 AI 单独说话,不是群聊
        流量。动作照样落审计。

        注入放到工作线程里跑:文本和 Enter 之间必须留一口气(见
        `MemberController.type_text`),那点等待不能卡住界面。
        """
        timeline = self.query_one("#timeline", Timeline)
        if self.controller is None:
            timeline.note("[总控台] 直连不可用(没接上 tmux),用 @名字 走群聊")
            return
        self._accept_input(raw)
        timeline.note(f"[直连] → human → {member}: {text}")
        self.run_worker(
            lambda: self._type_worker(member, text), thread=True, group="direct-type"
        )

    def _type_worker(self, member: str, text: str) -> None:
        """工作线程里真的去敲键盘;只有出问题才回报到时间线。"""
        assert self.controller is not None
        try:
            feedback = self.controller.type_text(member, text)
        except Exception as exc:
            self.call_from_thread(
                self._note, f"[直连] ✗ {member}: {type(exc).__name__}: {exc}"
            )
            return
        if not feedback.changed:
            self.call_from_thread(
                self._note, f"[直连] · {member}: 补过 Enter,去画面上确认一下是否已提交"
            )

    def _note(self, line: str) -> None:
        self.query_one("#timeline", Timeline).note(line)

    def send_from_input(self, raw: str) -> None:
        """把输入框里的一行送出去。

        三条路,按优先级:`/` 开头是命令;`@名字` 开头是群聊消息(发件人永远
        是 human);都不是的话——在成员会话里就**直接键入那个成员的终端**,在
        群聊会话里还是发给上一个对话对象。
        """
        timeline = self.query_one("#timeline", Timeline)
        if is_command(raw):
            self.run_command(raw)
            return
        addressed, text = split_address(raw)
        if not text:
            return
        if addressed is None and self.selected_member is not None:
            self.type_into_member(self.selected_member, raw, text)
            return
        target = addressed or self.last_target
        if target is None:
            timeline.note("[总控台] 还没有对话对象,先用 @名字 指定收件人")
            return
        try:
            deposit(Message.create(target, text, sender="human"), self.paths)
        except OSError as exc:
            timeline.note(f"[告警] bus 目录不可写:{type(exc).__name__}: {exc}")
            return
        self._accept_input(raw)
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
        # 人在别的会话里时,群聊那张卡上记未读数
        if self.selected_member is not None:
            self.unseen_traffic += 1
            self._sync_unread()

    # --- 滚动回看 --------------------------------------------------------

    def action_timeline_scroll(self, direction: str) -> None:
        """PgUp/PgDn/Home/End 翻的是**当前主画面**那一个。

        在成员会话里翻的是成员终端自己的回滚区(不用先 Tab 到画面上——那条路
        只有知道要按 Tab 的人才走得通)。
        """
        mirror = self._mirror()
        if self.selected_member is not None and mirror is not None:
            {
                "page_up": mirror.action_history_up,
                "page_down": mirror.action_history_down,
                "home": mirror.action_history_top,
                "end": mirror.action_history_bottom,
            }[direction]()
            return
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

    # --- 错误与恢复 ------------------------------------------------------

    def _on_fault_event(self, event: FaultEvent) -> None:
        fault = event.fault
        if event.recovered:
            self.query_one("#timeline", Timeline).note(
                f"[恢复] {fault.kind} {fault.target} 已恢复"
            )
            if fault.kind is FaultKind.BUS_UNWRITABLE and self.pump_enabled:
                self.pump.start()
            return
        self.query_one("#timeline", Timeline).note(
            f"[告警] {fault.kind} {fault.target}: {fault.detail}"
        )

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
        # attach 之前把窗口尺寸还给 tmux:我们钉死过它,不还的话贴上去尺寸对不上
        self._release_member_window(target)
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
