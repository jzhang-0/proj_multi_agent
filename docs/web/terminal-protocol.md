# 终端通道与接管协议设计稿(WEB-007 / WEB-008)

本文是 WEB-007(tmux 镜像、回滚与受限直连)和 WEB-008(成员管理、生命周期与完整接管)的实现依据：镜像帧格式与背压、多观看者共享订阅、canonical size 与租约关系、点击直连的服务端二次确认、按键白名单消息、PTY bridge 生命周期与安全约束、xterm.js 接入要点。

- 同系文档：[API 与实时协议设计](api-protocol.md)(WEB-003/004)。本文沿用其错误模型(§2.4)、认证会话(§6)与时间约定(§2.2)，不重复。
- 边界依据：[架构方案比较 §3](architecture-options.md#3-tmux-窗格在浏览器中的呈现)、[功能盘点 §2](inventory.md#2-tui-画面与操作总表)
- 租约依据：`src/control/lease.py`(WEB-002 已合入 `main`)
- 前端依据：[前端工具链调研](frontend-toolchain.md)，`@xterm/xterm` 6.0.0(MIT)
- 本文只写设计，不含实现。

## 1. 三条通道，为什么必须分开

| 通道 | 端点 | 语义 | 丢帧策略 |
|---|---|---|---|
| 控制面事件 | `/api/v1/stream` | 领域状态的 invalidation/delta | 合并成 resync(见 api-protocol §5.6) |
| 终端镜像 | `/api/v1/terminal/{member}/mirror` | **最新画面状态** | 只保留最新帧，旧帧直接丢 |
| 完整接管 | `/api/v1/terminal/{member}/attach` | **PTY 字节流** | 不可丢、不可乱序 |

分开的理由不是工程洁癖，是三种**背压语义互斥**：

- 镜像帧是全量状态，丢掉旧帧零损失，慢客户端只该掉帧、不该断线；
- PTY 字节流丢一个字节就花屏，只能靠 TCP 背压，宁可阻塞也不能丢；
- 控制面事件是有序增量，丢了要 resync。

把三者塞进同一个有界队列，必然出现"终端帧把任务事件挤掉"或"为了不丢字节而让画面积压过时帧"。**故三条独立 WebSocket，背压策略各自独立。**

## 2. 通用约定

- 帧封装：镜像与控制帧用 JSON 文本帧；PTY 字节流用**二进制帧**，不做 base64(省 33% 体积，且 xterm 能直接吃 `Uint8Array`)。
- `member` 是名册短名；服务端一律经 `workspace.session.session_for()` 翻译成会话名，**浏览器送来的 tmux target 一律不可信、不接受**(api-protocol §6.4)。
- 会话与鉴权同 api-protocol §6：WS 握手校验会话 cookie、`Host` 与 `Origin`。
- 握手校验失败时**必须先 `accept()` 再 `close(code, reason)`**；在 `accept()` 之前 `close()` 会退化成 HTTP 403，浏览器只看得到 `close(1006, "")`，拿不到码与原因(实测见 api-protocol §5.7)。关闭码与控制面流**同一套**：`4401` 未授权(cookie/`Host`/`Origin`)、`4404` 目标不存在(`member` 不在名册、tmux 会话不存在)、`4503` 暂不可用(工作区未登记、tmux 不可用、运行时未就绪)，前端只写一套判别。
- 服务端必须装有 WebSocket 实现(默认依赖取 `uvicorn[standard]`)：裸 `uvicorn` 下握手直接 404，本文件描述的所有通道都不成立；守住它的用例不能只用 `TestClient`(它在进程内自实现 WS，绕开服务端 WS 库)。
- 所有控制动作(按键、打断、终止、重启、接管)落 `AuditLog.record_control(action, target, changed=, detail=)`，与 TUI 同一套动作名：`type` / `key` / `interrupt` / `terminate` / `restart` / `takeover`。新增动作名要同步 `console.timeline` 的控制事件标签表，否则时间线上会显示成裸动作名。

## 3. 交互租约(对齐 WEB-002 已合入实现)

命名与语义直接采用 `src/control/lease.py`，不另造一套：

| 用法 | API |
|---|---|
| 每成员一把交互租约 | `MemberLeaseManager(leases_root(workspace))` |
| 取得/续期 | `acquire(member, owner, force=False)` → `LeaseState`；被占抛 `LeaseDenied` |
| 显式抢占 | `acquire(member, owner, force=True)` |
| 心跳续期 | `heartbeat(member, owner) -> bool` |
| 主动释放 | `release(member, owner)`(幂等) |
| 只读观察 | `holder(member)` / `holds(member, owner)` |

- **`owner` 取值定为 Web 会话 id + 连接 id**(如 `web:<session>:<conn>`)，不是进程标识。一个浏览器 tab 一个 owner，这样"同一台机器上两个 tab"也能正确互斥，与 TUI 进程平等参与抢占。
- resize、点击直连、按键、完整接管**共用同一把租约**，与 `MemberLeaseManager` 的文档语义一致。镜像只读**不需要**租约。

### 3.1 心跳必须由活连接驱动(重要)

`Lease` 的自动回收看两件事：心跳是否在 `ttl`(默认 `DEFAULT_TTL_SECONDS = 15.0` 秒)内，以及**持有者 pid 是否还活着**。

Web 场景下 pid 是 **web 服务端进程**的 pid，浏览器崩溃或断网时它照样活着。所以 pid 探测在这里**探不到浏览器掉线**，唯一有效的回收路径是心跳超时。由此得出一条硬规则：

> **心跳只能由活着的 mirror/attach WebSocket 连接驱动，不得由后台定时器无条件续期。**
> 连接关闭(正常或异常) → 立即 `release()`；连接失联而未触发 close 回调 → 心跳自然停止，≤15s 后租约可被他人回收。

若实现成"服务端起个 timer 帮所有已发放租约续期"，掉线的 tab 会永久占住成员，别人只能 `force=True` 抢占——功能上还能救，但"崩溃/断线自动释放"这条 WEB-002 验收就是假的。

### 3.2 抢占

- 客户端请求 `{"type":"lease","action":"acquire","force":false}`；被占时服务端回 `{"type":"lease_denied","holder":{"owner":..., "host":..., "acquired_at":...}}`，前端提示"当前由 X 控制"，并提供"强制接管"按钮 → 重发 `force:true`。
- **抢占永远是显式的**，服务端不得因为"新客户端更活跃"自动 force。
- 抢占成功后，原持有者的下一次 `heartbeat()` 返回 `False` → 服务端立刻向其推送 `{"type":"lease_lost"}`，前端退出直连态、镜像降级为只读。原持有者若在 attach 中，则按 §8.4 断开 PTY。

## 4. 镜像通道(WEB-007)

### 4.1 帧格式

服务端 → 客户端：

```json
{"type": "frame", "member": "sol", "frame_seq": 128,
 "cols": 120, "rows": 40, "history_offset": 0,
 "captured_at": 1756512000.14,
 "cursor_y": 36,
 "input_rows": [35, 36], "live_allowed": true,
 "encoding": "ansi", "data": "\u001b[H\u001b[J...<capture-pane -p -e 全文>..."}
```

- `data` 是 `PaneSnapshotter.capture(member, color=True, start=...)` 的**完整帧**，服务端在前面拼上 `\u001b[H\u001b[J`(归位 + 清屏)。这是架构 §3.1 推荐的第二种渲染方式：交给终端组件，不在浏览器里解析 SGR。
- **不用 `PaneOutputStream` 的原始增量输出**。原始输出含重绘序列，断线、丢帧、中途订阅后无法重建屏幕；它只适合判活(`ActivityTracker` 已经这么用)。
- `frame_seq` 每成员单调递增。客户端**只渲染 seq 更大的帧**，迟到帧直接丢——这是防止乱序把旧画面盖到新画面上的唯一保险。
- `history_offset > 0` 表示回滚态；此时 `live_allowed` 恒为 `false`。
- `input_rows` 是服务端算好的可点击行号(相对本帧顶端，0 基)，见 §6。
- `cursor_y` 见 §12.1 的已知限制。

### 4.2 ≤10 Hz 与"无变化不发"

- 采集频率由 `PaneSnapshotter(min_interval=...)` 控制。TUI 当前是 `MIRROR_INTERVAL = 0.08`(定时器)配 `min_interval = 0.04`；**Web 侧定为 `min_interval = 0.1`(10 Hz 上限)**，与架构 §3.1 一致——浏览器渲染成本和带宽都比本机 Textual 高。
- **画面文本与上一帧完全相同时不发帧**。AI CLI 大部分时间静止，静止时发帧纯属浪费带宽与 xterm 重绘。改为每 5s 发一次 `{"type":"idle","frame_seq":N}` 让客户端确认连接仍活。
- **无观看者时停止采集**：最后一个观看者断开 → 停掉该成员的采集循环，不再打扰 tmux。对齐 TUI"看不见就不拉"的既定取舍。

### 4.3 多观看者共享单一订阅

`PaneSnapshotter` 已经把**同参数的并发请求**合并成一次 `capture-pane`(缓存 key 是 `(target, color, start, end)`)，但那是"请求去重"，不是"一个采集多播"。若每个 WS 连接各起一个采集循环，N 个观看者就有 N 个定时器，唤醒时刻错开时缓存命中率会掉，tmux 调用次数随观看者线性增长。

设计为**单生产者 + 多消费者扇出**：

```text
MirrorBroadcaster(member, start)   ← 每个 (成员, 回滚偏移) 一个
   ├─ 采集循环: PaneSnapshotter.capture(member, color=True, start=start) @10Hz
   └─ 扇出: 逐个订阅者的有界队列(maxsize=1)
```

- **分组键是 `(member, history_offset)`，不是 `member`**。回滚偏移直接决定 `capture-pane -S` 的起点，不同偏移的观看者看的是不同内容，不能共享一份帧。
- 实时观看者(`history_offset == 0`)天然聚在同一组，共享一次 capture——这是绝大多数情况。
- 回滚是低频手动操作：回滚组的采集频率降到 **2 Hz** 就够，且历史区内容本就不变。
- 组内订阅者归零 → 该组采集循环退出。

### 4.4 背压：只保留最新帧

每个订阅者一个 `asyncio.Queue(maxsize=1)`。新帧到达时**先清空再放入**，不等待、不阻塞采集循环。

慢客户端的表现是掉帧(画面更新变慢)，不是断线、不是积压过时画面。这与 api-protocol §5.6 的"合并成 resync"不同，因为镜像帧本身就是全量状态——丢掉旧帧没有任何信息损失，无需 resync 概念。

### 4.5 回滚

客户端 → 服务端：`{"type":"scroll","offset":120}`

- `offset` 是往回翻的行数，范围 `[0, 2000]`，上限沿用 `console.mirror.HISTORY_LIMIT = 2000`(tmux 默认回滚区大小)。
- 服务端换算成 `capture-pane -S -<offset>`，即 `PaneSnapshotter.capture(..., start=-offset)`。**读的是 tmux 自己的回滚区，不另造 Web 历史。**
- 滚动步长(PgUp 10 行、滚轮 3 行)是前端交互，不进协议；协议只收最终 offset。
- **回滚不需要租约**，因为它只改本连接自己的视图，不产生任何 tmux 副作用(只是不同的 `-S` 参数)。
- `offset > 0` 时服务端**拒绝一切输入**并回 `{"type":"denied","reason":"scrolled-back"}`，对齐 TUI 的 `Mirror.set_live_input()`(回滚态永不允许激活)。

## 5. canonical size 与"终端窗口适配"

tmux window 的尺寸是**每成员全局唯一**的：`fit_window()` 会先把 `window-size` 设成 `manual` 再 `resize-window`。若每个前端各按自己的 viewport 去 fit，TUI 与多个浏览器 tab 会互相盖写，成员 CLI 持续重排——这正是架构 §3.1 点名的问题。

规则：

1. **只有交互租约持有者的 viewport 决定 canonical size**。其他观看者按比例留白(letterbox)或裁切，**不发 resize**。
2. 客户端消息 `{"type":"resize","cols":120,"rows":40}`；服务端先查 `holds(member, owner)`，不持有则回 `{"type":"denied","reason":"no-lease"}`，**不静默忽略**(静默会让前端以为尺寸已生效)。
3. 尺寸低于 `MIN_FIT_SIZE = (60, 15)` 时忽略，对齐 TUI——太小的窗口 fit 过去反而让 CLI 排不下。
4. 尺寸与上次相同时不重复下发。`resize-window` 每次都是一次 tmux 进程启动。
5. 租约释放、易主、或该成员最后一个观看者离开时，调用 `release_window_size(target)` 把尺寸交还 tmux。
6. **attach 之前必须先 `release_window_size`**，见 §8.2。

## 6. 点击直连：行号必须经服务端按最新帧再确认

### 6.1 为什么不能信帧里的 `input_rows`

帧最长可能是 100ms 前的。这期间成员 CLI 完全可能从 composer 切到了一个确认弹窗——那一行不再是输入框，而是"确定/取消"。此时把用户的按键送进去，等于替人盲点确认。架构 §3.2 明确要求"服务端必须再次用最新帧确认该行仍是可输入区"。

### 6.2 协议

客户端 → 服务端：

```json
{"type": "focus_input", "frame_seq": 128, "row": 36}
```

服务端按顺序检查，任一不过即拒绝：

1. 持有该成员交互租约(`holds(member, owner)`)，否则 `reason: "no-lease"`；
2. `history_offset == 0`，否则 `reason: "scrolled-back"`；
3. `frame_seq` 与当前最新帧相差 **≤ 3 帧**(约 300ms)，否则 `reason: "stale-frame"`，要求基于新帧重点；
4. **重新采集一帧**，剥离 ANSI 后重算 `terminal_input_rows()`，`row` 必须仍在结果中，否则 `reason: "row-not-input"`。

通过则回 `{"type":"live","active":true}`，进入直连态。

### 6.3 行号只用于授权，不产生 tmux 命令

tmux 没有"点击定位到某行"的概念，`row` **不会**被翻译成任何 `send-keys` 或光标移动。它唯一的作用是回答"用户点的是不是输入区"——是，就允许后续按键流；不是，就拒绝。实现者不必去找定位命令。

### 6.4 ANSI 剥离必须与 TUI 等价

`terminal_input_rows()` 匹配的是**纯文本**：TUI 里 `Mirror.show_screen()` 存的 `screen_text` 是 `Text.from_ansi(ansi).plain`，`click_hits_input()` 拿它做判定。

服务端只采集一次带色帧(不为识别再多打一次 `capture-pane`)，剥离 ANSI 后喂给识别函数。**剥离结果必须与 `Text.from_ansi(...).plain` 逐字符一致**，否则同一画面 TUI 认得出 composer、Web 认不出(或反之)，就成了两套规则。

建议：剥离函数与 `terminal_input_rows` 一起放进控制面(WEB-001 已要求下沉该识别函数)，并用同一份带 ANSI 的样本同时断言"控制面剥离结果 == `Text.from_ansi().plain`"。这条钉子测试比任何文档都管用。

## 7. 直连输入

### 7.1 消息形状

客户端 → 服务端：

```json
{"type": "input", "kind": "text",   "data": "abc"}
{"type": "input", "kind": "key",    "name": "BSpace"}
{"type": "input", "kind": "submit"}
```

`name` 白名单**严格等于** `MemberController.press_key()` 现有的九枚：

```text
Enter  Tab  BTab  BSpace  DC  Up  Down  Left  Right
```

服务端**再校验一次**白名单，不因为前端已经过滤就放行——`press_key()` 自己也会对越界值抛 `ValueError`，两道防线都要在。

### 7.2 为什么直连态不用 xterm 的原始字节

xterm 的 `onData` 给的是终端字节序列(方向键是 `\u001b[A` 之类)。若直连通道收字节，服务端就得反解 ANSI 去猜按键，才能套白名单——把一个已经解决的问题重新变成解析问题。

故：**直连态前端拦截 `keydown`，按固定映射表转成结构化消息**，映射与 TUI `Mirror._on_key` 一一对应：

| 浏览器按键 | 消息 |
|---|---|
| 可打印字符 | `kind:"text"` |
| `Tab` / `Shift+Tab` | `key: Tab` / `BTab` |
| `Backspace` / `Delete` | `key: BSpace` / `DC` |
| `↑ ↓ ← →` | `key: Up/Down/Left/Right` |
| `Enter` | `kind:"submit"` |
| `Escape` | 退出直连态(本地动作，不发送) |
| `Ctrl+*` / `Meta+*` | 不拦截，交给浏览器与全局快捷键 |

xterm 在镜像面板中设 `disableStdin: true`，只做显示。
**完整接管通道相反**：那里是真 PTY，用户要的就是完整终端，白名单不适用，直接透传 xterm 原始字节(§8)。这是两条通道的第二个本质差异。

### 7.3 合并与串行

- 服务端每成员一个**串行输入队列**，所有输入经 `tmuxctl` 顺序执行，绝不并发 `send_keys`。
- 相邻 `text` 消息合并成一次 `send_keys -l`，合并窗口沿用 TUI `_drain_live_input()` 的 **8ms**。不合并的话每个字符启动一次 tmux 进程，本机并行跑着几个 AI CLI 时代价明显。
- `submit` 走 `MemberController.submit_live_text()`：先等 `LIVE_SUBMIT_GAP_S = 0.01` 再发 Enter，然后 `KeyInjector.ensure_submitted()` 确认。**这个间隔不能省**——Claude/Codex 这类 CLI 会把同一批里的 Enter 吞进粘贴判定，只在输入框里换行而不提交。
- `ensure_submitted` 返回未提交时，服务端向客户端回 `{"type":"notice","text":"提交未确认，请查看成员输入区"}`，与 TUI 的提示对齐。

### 7.4 审计口径

沿用 TUI 现状，不加码也不减码：

- `text` **不逐字记审计**(每个字符一条会把 `bus/log.jsonl` 冲垮，也会把时间线刷屏)；
- `key` 每次记一条 `action="key"`；
- `submit` 记一条 `action="type"`，`detail` 是提交的整段文本；
- 失败(异常、目标消失)记 `changed=false` + 异常摘要。

## 8. 完整接管：PTY bridge(WEB-008)

F8 的 `MemberController.takeover()` 把**本机 TTY** 交给 `tmux attach`，浏览器没有可继承的 TTY，不能直接复用。Web 侧需要后端自建 PTY。

### 8.1 端点与前置

`WS /api/v1/terminal/{member}/attach`

建立前依次检查：会话 cookie → 交互租约(`acquire`，被占则 `LeaseDenied` → 提示抢占) → `Tmux.has_session(session)`。任一不过即拒绝升级，不创建任何进程。

### 8.2 启动序列

1. **先 `release_window_size(target)`**。`fit_window()` 会把 `window-size` 设成 `manual` 并钉死尺寸；不还回去，attach 上来的客户端尺寸就对不上(TUI 的 `_release_member_window()` 正是为此存在，注释里写着"F8 接管前必须还")。
2. `session = session_for(tmux, member)`，argv 固定为：
   ```python
   tmux.command_argv("attach-session", "-t", f"={session}")
   ```
   `=` 前缀是 tmux 的精确匹配，防止会话名前缀歧义误连到别的会话(现有 `takeover()` 已经这么写)。
3. `pty.openpty()`，fork/exec 上述 argv。**不经 shell**(argv 数组，`shell=False`)，**不接受任何来自浏览器的命令、参数或会话名**。
4. `TIOCSWINSZ` 设 PTY 尺寸为浏览器 xterm 的 `cols/rows`。
5. 子进程环境用**白名单**构造，不整份继承 web 服务端环境：`TERM`(收敛为 `xterm-256color`)、`PATH`、tmux socket 相关变量。不传 web 会话 token，不传与该成员无关的内部变量。
6. 记审计 `record_control("takeover", member, changed=True, detail="web attach")`。

### 8.3 数据面

- PTY → WS：二进制帧，原样转发。
- WS → PTY：二进制帧，原样写入。此通道**不套白名单**(见 §7.2)。
- resize：`{"type":"resize","cols":120,"rows":40}` → `TIOCSWINSZ`。attach 期间 tmux 客户端尺寸由 PTY 决定，**不再调用 `fit_window`**。

### 8.4 结束序列(断线、抢占、主动退出走同一条路)

1. 关闭 PTY 主端 → 子进程收到 SIGHUP，`tmux attach-session` 客户端退出；
2. `waitpid()` 回收子进程，不留僵尸；
3. `MemberLeaseManager.release(member, owner)`；
4. 记审计 `record_control("takeover", member, changed=..., detail="断线释放" / "正常退出")`；
5. 关闭 WS。

**退出关的是 tmux client，不是 session。** 绝不调用 `kill-session`——这是本节最容易写错的一处，也是 inventory「退出」条目的硬要求："Web 只应断开会话，不触发 down"。同理，attach 通道断开不得触发 `Lifecycle.down()`。

### 8.5 并发

每成员至多一个 attach(由交互租约天然保证)。第二个请求要么被拒，要么显式 `force` 抢占；抢占时先对原持有者走完整的 §8.4 序列，再放行新连接。

attach 期间，同一成员的 mirror 直连态自动降级为只读——两者共用一把租约，租约易主时原持有者收到 `lease_lost`。

## 9. 成员管理与危险动作(WEB-008)

写操作走 HTTP，不走 WS(与 api-protocol §5.2 同一理由：写要审计、要幂等、要错误回执，请求/响应配对更难写错)。

| 动作 | 端点 | 危险 | 控制面入口 |
|---|---|---|---|
| 打断 | `POST /api/v1/members/{name}/interrupt` | 否 | `MemberController.interrupt()` |
| 终止 | `POST /api/v1/members/{name}/terminate` | **是** | `MemberController.terminate()` |
| 重启 | `POST /api/v1/members/{name}/restart` | **是** | `MemberController.restart()` |
| 拉起 | `POST /api/v1/members/{name}/up` | 否 | `Lifecycle.up(name)` |
| 关闭 | `POST /api/v1/members/{name}/down` | **是** | `Lifecycle.down(name)` |
| 收编 | `POST /api/v1/members/adopt` | 否 | `SessionAdopter` |
| 静音 | `POST /api/v1/members/{name}/mute` | 否 | `MutePolicy` / Hub |

### 9.1 二次确认必须由服务端强制

前端弹窗可以被绕过(直接打 API)，而"终止"和"关闭"会杀掉正在干活的 AI 进程。故：

1. `POST .../terminate/confirm` → 返回一次性 `confirm_token`(30s 有效，绑定 actor + member + action)；
2. `POST .../terminate` 请求体带该 token，服务端校验并作废。

好处是审计里能确证"确认过"，而不是只能记录"收到了一个删除请求"。非危险动作(打断、拉起)不需要这一步。

### 9.2 其他约束

- 全部动作落 `AuditLog.record_control`，动作名与 TUI 一致。
- `/adopt` 收编的是名册外、已存在、名称合法的 tmux 会话，是**进程级临时状态、重启不保留**。Web 必须在界面上明示这一点，且这类成员在 snapshot 里 `source: "adopted"`(api-protocol §4.10)。
- `/mute` 走 Hub 策略层，拒收与回执照常进审计与时间线，不在 Web 层另做一套过滤。
- 成员名一律校验在"名册 ∪ 已收编"集合内，不拼进任何命令行。

## 10. xterm.js 接入要点

`@xterm/xterm` 6.0.0(MIT)，npm 构建期下载，esbuild 打进 `app.js`，CSS 复制到本地 `assets/`。**运行时不访问 CDN，不需要 Node**([frontend-toolchain.md](frontend-toolchain.md) 已验证 wheel 打包路径)。

| 场景 | 配置 |
|---|---|
| 镜像面板 | `disableStdin: true`；每帧 `term.write(归位清屏 + data)`；输入走外层 `keydown`(§7.2) |
| 完整接管 | `disableStdin: false`；`term.onData(d => ws.send(d))`；`term.write(bytes)` |
| 两者 | `convertEol: false`(tmux 给的是完整终端序列，再转换会重复换行) |

- **禁 `innerHTML`**：xterm 自管 DOM，任何时候都不要把终端文本塞进 `innerHTML`。这既是 XSS 面，也会破坏 xterm 的渲染状态。
- **主题不由服务端下发**：xterm `theme` 用前端自己的 token，与 api-protocol §4.8 的"颜色属呈现层"一致。
- **终端 bundle 按需加载**：`app.js` 未压缩已 357KB，其中 xterm 占大头。非终端页面不该为它买单，终端面板首次打开时再动态 import。
- **addon 一个都不预设引入**。`addon-fit`、`addon-unicode11`、WebGL 渲染器都不是必需依赖；要引入须单独锁版本并复核许可证与浏览器兼容性(工具链调研的结论)。相应地：
  - 尺寸换算不用 `addon-fit`，用一个隐藏的等宽测量元素算出 cell 宽高，再由容器尺寸算 `cols/rows`；
  - **CJK 宽度必须实测**。不引 `unicode11` 时 xterm 用内置宽度表，成员输出里大量中文和 emoji，宽度算错会整屏错位。这一项列为 WEB-007 的视觉自验证必查项(见 §11)。

## 11. 验证要求

WEB-007/008 是"高"难度项，测试通过不能替代看图([Goal 总索引](../goals/README.md)「视觉自验证」)。至少覆盖：

- 真实 tmux 会话下，TUI 与浏览器**同时观看**同一成员，画面一致；
- 两个浏览器 tab 抢占交互租约，被抢者收到 `lease_lost` 并退出直连态；
- **杀掉持租约的浏览器进程**(不是正常关闭 tab)，≤15s 后另一端可正常 `acquire`——这条专门验 §3.1 的心跳规则；
- 回滚态下发送按键被拒；
- 点击一个"上一帧是输入框、当前帧已变成弹窗"的行，被 `row-not-input` 拒绝；
- attach 断线后成员会话仍在(`has_session` 为真)，CLI 进程未被杀；
- 中文与 emoji 密集的成员画面截图核对宽度。

## 12. 已识别的风险与待验证项

1. **完整帧不含光标列**。`capture-pane -e` 只给画面，`capture_with_cursor()` 额外给 `cursor_y`(行)，但**没有 `cursor_x`**。直连态用户看不到光标落在哪一列，输入体验会明显不如 attach。可选缓解：多取一个 `display-message -p "#{cursor_x}"`(可与 capture 用 `;` 串成一次 tmux 调用，参考 `capture_with_cursor` 的写法)。**列为 WEB-007 待验证项**，若代价可接受就在帧里补 `cursor_x`。
2. **capture 帧与真实终端有细微差异**。`capture-pane -e` 输出的是 SGR 着色的当前网格，不含 DECSET 等终端模式；vim 这类全屏程序在 xterm 里重放可能与原终端有出入。attach 通道无此问题。**列为已知限制**，不是缺陷——镜像的定位本就是"看"，要精确交互就用完整接管。
3. **ANSI 剥离等价性**是点击直连正确性的单点。§6.4 已给出钉子测试写法；若两端规则漂移，症状是"看得见输入框但点不进去"，很难从现象反推原因。
4. **PTY bridge 的进程回收**。忘记 `waitpid` 会攒僵尸进程，长时间运行的 web 服务端上会累积。§8.4 第 2 步不可省。
5. **`fit_window` 把 `window-size` 设为 `manual` 是全局副作用**，不会因为 web 进程退出而自动恢复。若 web 崩溃时未走到 `release_window_size`，成员窗口会被永久钉在某个尺寸，TUI 之后看到的画面也不对。建议 WEB-007 在成员采集循环退出与进程退出路径上都调用 `release_window_size`，并在 §11 的验证里加一条"kill -9 web 进程后 TUI 画面尺寸恢复正常"。
6. **`terminal_input_rows` 只认 Claude 双横线 composer 与 Codex 底部 `›`**。名册里出现第三类 CLI 时，Web 的点击直连会静默失效(返回空元组，点哪都不进直连态)。这不是本文引入的限制，但 Web 上表现为"功能好像坏了"，界面应给出可见提示而不是无反应。

## 13. 待 WEB-005 / WEB-007 落地后校正

- [ ] `cursor_x` 是否进帧(§12.1 实测后定)
- [ ] CJK/emoji 宽度是否需要 `unicode11` addon(§10 实测后定)
- [ ] 镜像帧的实际带宽与 10 Hz 是否需要下调(真实 40×120 画面实测)
- [ ] 尺寸换算是否自行测量足够稳定，还是引入 `addon-fit`
- [ ] 抢占的前端提示文案与 TUI 的控制反馈是否需要统一词表(参照 api-protocol §4.3 的 vocabulary 端点)
