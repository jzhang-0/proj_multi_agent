# Web 版功能盘点

> 调研任务：T-001（2026-08-30）
>
> 范围依据 human 最新拍板：Web 与 TUI 并列，目标是对齐 TUI 的全部现有功能；手机 UI 暂缓。本文是实现前的库存和接口边界，不改现有代码，也不把当前 `gateway/page.py` 的手机群聊页当成最终 Web 产品。

## 1. 结论摘要

- 当前 TUI 已经同时承担四类职责：任务责任链、工作对话记录、成员终端镜像/控制、工作区与成员管理。Web 版不能只复刻 gateway 的聊天时间线。
- 现有数据已经分层：`bus` 提供消息队列与审计，`work` 提供任务事件流，`roster` 提供有效成员与生命周期，`team` 提供 Leader/成员角色和启动适配，`tmuxctl` 提供成员终端和进程控制。Web 层应调用这些 Python 服务/接口，不读取 TUI 组件内部状态。
- `gateway/page.py` 目前只有一个单页聊天客户端：本地保存名字/口令、长轮询消息、发送文本和断线游标续传。它没有任务、成员状态、终端镜像、控制操作、图片附件或分类筛选。
- 最难对齐的不是普通 CRUD，而是 tmux 相关实时能力：终端颜色保真镜像、回滚、点击原生输入区直连、按键顺序透传、窗口 fit、attach 接管，以及相应的权限/并发/断线语义。应先把 Web API 的只读快照与操作命令抽出来，再实现浏览器传输层。

## 2. TUI 画面与操作总表

“只读”表示 Web 只展示状态；“操作”表示会写队列、账本、名册、审计或控制 tmux；“只读 + 操作”表示同一面板同时有两类能力。来源列中的接口均为当前实现中的可复用入口。

下表是 WEB-009 终验（T-023，2026-08-30）后的 `main` 状态核对结果：WEB-006/WEB-007/WEB-008 均已 Leader 验收合入，本表相应行统一标注`已实现`。发布制品验证（sdist/wheel 含前端产物、`qa.release` 源码外安装、退出真实 tmux 验证）见 WEB-009 收口清单与 `docs/goals/web.md` 证据，不在本表逐条重复。

| TUI 面板/能力 | 当前行为 | 交互类型 | Web 对齐所需来源 | 难度 | 当前状态 |
|---|---|---|---|---|---|
| 顶部 Header / 工作区副标题 | 显示“总控台”、工作区 slug 和项目根；未登记时显示未登记提示 | 只读 | `workspace.resolve`、`Workspace`；`ConsoleApp._workspace_subtitle()` / `_workspace_banner()` | 低 | 已实现（WEB-003/005） |
| 左栏任务摘要 | 绑定团队后显示 Leader、进行中、待验收、阻塞/退回计数 | 只读 | `WorkService.snapshot()`、`Team.leader`；`render_task_summary()` | 低 | 已实现（WEB-003/005） |
| 左栏工作对话记录卡 | 显示入口、未读数、当前是否正在查看 | 只读 | `ConsoleApp.unseen_traffic`、`ConversationCard` | 低 | 已实现（WEB-004/005） |
| 左栏成员卡片 | 显示 `idle/working/stuck/dead/failed` 图形与颜色、排队数、最后活动相对时间 | 只读 | `MemberStatusService.snapshot()`、`pending_counts()`；下层 `ActivityTracker` / `HealthSupervisor` | 中 | 已实现（WEB-003/004） |
| 左栏任务列表 | 每个任务显示 ID、状态、标题、执行者、评审者；点击/键盘选择 | 只读 + 操作（选择） | `WorkSnapshot.tasks`、`TaskStatus`、`STATUS_LABELS`；`TaskCard` | 低 | 已实现（WEB-003/005） |
| 中栏任务详情 | 显示状态、唯一 Leader、执行/评审、创建/更新时间/完成时间、父子任务、说明 | 只读 | `WorkSnapshot.get()`、`Task`、`children()`；`TaskDetail.show_task()` | 低 | 已实现（WEB-003/005） |
| 任务证据 | 逐条显示成员追加的证据引用 | 只读 | `Task.evidence`、`WorkEvent(EVIDENCE)` | 低 | 已实现（WEB-003/005） |
| 不可覆盖任务事件流 | 显示建立、拆分、派工、进展、阻塞、证据、提交、评审、退回、改派、接管、验收、汇报及详情 | 只读 | `WorkLedger.load()` 重放 `work/events.jsonl`；`WorkSnapshot.events_for()`；`event_details()` / `EVENT_LABELS` | 低 | 已实现（WEB-003/005） |
| 任务关联工作对话 | 任务详情中显示同一 task ID 的总线沟通及图片数量 | 只读 | `AuditLog.entries()`，筛选 `event=deposit` 和 `task`；`Message.task` / `attachments` | 低 | 已实现（WEB-003/005） |
| 工作对话记录时间线 | 实时追加总线流量；启动时从 `bus/log.jsonl` 回填；显示结局、原因、@ 高亮、分钟分组 | 只读 | `AuditLog.entries()`、`AuditLog.read_body()`、`DeliveryResult`；`TimelineEntry` / `history()` | 中 | 已实现（WEB-004/005） |
| 时间线分类筛选 | `全部 / human / AI / 任务 / 控制`，显示各分类计数；点击或左右键切换；不靠正文猜分类 | 只读 + 操作（筛选） | `TimelineEntry.resolved_category`（收发端/审计事件/WorkEvent 类型）`；`ConversationFilter` / `Timeline.set_category()` | 低 | 已实现（WEB-005） |
| 时间线滚动 | PgUp/PgDn、Home/End、Ctrl+↑/↓；不在底部时新消息不强行拉回 | 只读 + 操作（视图滚动） | 前端滚动状态；历史来自 `AuditLog` 与 `WorkLedger` | 低 | 已实现（WEB-005） |
| 工作对话输入框 | 回车发送；绑定团队时默认发 Leader 并自动关联选中任务；无 @ 时发上一个对话对象 | 操作 | `Message.create()`、`deposit()`、`BusPaths`；`WorkService.team.leader`；`ComposeInput` | 中 | 已实现（WEB-006） |
| @ 成员补全 | 输入 @ 展示全体成员，Tab/↑↓ 选择，回车落定；支持中文成员名 | 操作（本地 UI，最终可发送） | `member_names()` / `Roster.enabled_members()`、`ComposeInput` 的 `matching_members()` | 低 | 已实现（WEB-006） |
| 发送 ask/reply | 总线支持 ask id、关联回复和阻塞等待；成员收到回复指引 | 操作 | `bus.ask.store_ask()` / `store_reply()` / `wait_for_reply()`；`Message.kind/reply_to` | 中 | 已实现（WEB-006） |
| 图片粘贴/发送 | Ctrl+V 读取系统剪贴板图片；PNG 内容寻址落盘；支持纯图片或图文；最多 8 张 | 操作 | `ClipboardImageStore`、`Attachment`、`Message.attachments`、工作区 `attachments/` | 中（浏览器上传协议需设计） | 已实现（WEB-006） |
| 待发图片撤销 | 文字为空时 Backspace/Delete 逐张撤销引用；不删除内容寻址文件 | 操作（本地 UI） | `ComposeInput.remove_last_attachment()`；附件文件由 `ClipboardImageStore` 管理 | 低 | 已实现（WEB-006） |
| 成员终端镜像 | 选中成员后显示带 ANSI 颜色的 tmux 当前画面；活跃刷新 ≤100ms；不可见时停止拉取 | 只读 | `PaneSnapshotter.capture(member, color=True)`、`PaneSnapshot.text`；`Mirror.show_screen()` | **高** | 已实现（WEB-007） |
| 成员终端回滚 | PgUp/PgDn、Home/End、滚轮、Ctrl+↑/↓ 读取成员自身 `capture-pane -S` 历史；回滚时禁用实时输入 | 只读 + 操作（视图滚动） | `PaneSnapshotter.capture(..., start=...)`、`Mirror.history_offset` | **高** | 已实现（WEB-007） |
| 终端窗口适配 | 镜像可见时按 Web 主画面尺寸 `fit_window`；离开/attach 前释放尺寸；小尺寸不调节 | 操作（后台副作用） | `Tmux.fit_window()` / `release_window_size()`；`ConsoleApp._fit_member_window()` | **高** | 已实现（WEB-007） |
| 成员画面点击直连 | 仅识别当前 Claude 双横线 composer 或 Codex 底部 `›` 输入行；点击输入区才进入实时态，点击历史/输出区无效 | 操作 | `terminal_input_rows()`、`Mirror.click_hits_input()`；需要浏览器坐标和终端网格映射 | **高** | 已实现（WEB-007） |
| 直连文本输入 | 直连态普通字符按顺序进入成员终端，相邻字符可合并；Enter 单独提交并确认 | 操作 | `MemberController.insert_text()` / `submit_live_text()`；`Tmux.send_keys(literal=True)`、`KeyInjector.ensure_submitted()` | **高** | 已实现（WEB-007） |
| 直连编辑/方向键 | Tab/Shift+Tab、↑↓←→、Backspace/Delete/Forward Delete、空 Enter 按白名单透传；每个控制动作进入审计 | 操作 | `MemberController.press_key()`；白名单 `Enter/Tab/BTab/BSpace/DC/Up/Down/Left/Right`；`AuditLog.record_control()` | **高** | 已实现（WEB-007） |
| 成员打断 | F5 或对应 Web 按钮发送 Escape + Ctrl-C | 操作 | `MemberController.interrupt()` → `ProcessController.interrupt()`；审计 `control` | 中 | 已实现（WEB-008） |
| 成员终止 | F6；二次确认后向 CLI 进程发送 SIGTERM | 操作（危险，需确认） | `ConfirmControlScreen` 语义；`MemberController.terminate()` → `ProcessController.terminate()`；审计 | 中 | 已实现（WEB-008） |
| 成员重启 | F7；二次确认后 `Lifecycle.restart()` | 操作（危险，需确认） | `MemberController.restart()` → `Lifecycle.restart()`；`Roster`、`Tmux`、审计 | 中 | 已实现（WEB-008） |
| 全屏接管 / attach | F8 挂起 TUI，执行 `tmux attach-session`，退出后返回；先释放 fit 尺寸 | 操作（高风险/桌面专属） | `MemberController.takeover()`、`Tmux.command_argv()`、`session_for()` | **高/需 Web 方案** | 已实现（WEB-008） |
| `/up` | 拉起指定成员，已运行则跳过 | 操作 | `Lifecycle.up(name)`；`Roster` + `start_member()` + `Tmux` | 中 | 已实现（WEB-008） |
| `/down` | 关闭指定成员 tmux 会话 | 操作（危险） | `Lifecycle.down(name)` → `stop_member()`；`Tmux.kill_session()` | 中 | 已实现（WEB-008） |
| `/restart` | 关闭并重新拉起指定成员 | 操作（危险） | `Lifecycle.restart(name)`；`Roster` / `Tmux` | 中 | 已实现（WEB-008） |
| `/adopt` | 将名册外、已有且名称合法的 tmux 会话收编为本进程临时成员；重启不保留 | 操作 | `SessionAdopter.discover/adopt/member_names()`、`Tmux.list_panes()` | 中 | 已实现（WEB-008） |
| `/mute` | 策略层拒收指定成员消息，再执行一次取消；拒收和回执进入审计/时间线 | 操作 | `MutePolicy`、`OutboundPolicy`、`Hub`、`receipt_for()`、`AuditLog` | 中 | 已实现（WEB-008） |
| `/workspace` | 切换绑定工作区；总线、成员、任务、时间线、附件目录和 tmux 命名空间一起切换 | 操作 | `require_slug()`、`BusPaths.for_workspace()`、`WorkService.for_workspace()`、`SessionNames`、`load_effective_roster()` | 中 | 已实现（WEB-005） |
| `/member add/rm/list` | 增减或列出当前工作区成员；更新 `members.toml`，重建成员栏和补全 | 操作 | `workspace.members.add_member/remove_member`、`load_effective_roster()`、`Roster` | 中 | 已实现（WEB-008） |
| `/task [ID]` / F3 | 打开任务看板或指定任务详情；当前任务决定输入框关联 task ID | 操作（切换视图） | `WorkService.snapshot()`、`ConsoleApp.select_work()`、`TaskDetail` | 低 | 已实现（WEB-005） |
| `/help` / ? / F1 | 显示命令、快捷键和安全语义；可滚动关闭 | 只读 + 操作（打开/滚动） | `COMMANDS`、`SHORTCUT_GROUPS`、`ShortcutHelpScreen` | 低 | 已实现（WEB-005） |
| 深浅主题 / T | 深色和浅色主题切换；历史内容按 token 重绘 | 操作（本地 UI） | `console.theme.THEMES`、`Timeline.rerender()` | 低 | 已实现（WEB-005） |
| 健康告警 | tmux server、成员会话、bus 可写性故障/恢复边沿提示；恢复后总线继续投递 | 只读 + 操作（自动恢复） | `ConsoleHealthMonitor`、`FaultEvent`、`HealthSupervisor`、`BusPump.start()` | 中 | 已实现（WEB-003/004） |
| 成员状态实时更新 | 0.5s 刷新卡片；成员输出/成功投递/死亡/failed 更新状态；自动拉起可配置且三次失败熔断 | 只读 + 操作（自动恢复） | `MemberStatusService`、`ActivityTracker`、`HealthSupervisor` | 中 | 已实现（WEB-003/004） |
| 退出 | Q/Ctrl-C 只停 bus pump 和 UI，不关闭成员会话 | 操作 | `BusPump.stop()`、`ConsoleApp.on_unmount()`；Web 侧退出只停 uvicorn，不触发 down | 低 | 已实现（WEB-005；真实 tmux 验证见 WEB-009 后半，T-023） |

## 3. 当前 `gateway/page.py` / LocalChat 能力

### 3.1 页面已有能力

文件：`src/gateway/page.py:10-138`。

| 能力 | 当前实现与限制 |
|---|---|
| 页面交付 | `PAGE_HTML` 是内嵌单文件 HTML；CSS/JS 无外部资源，`GET /` 和 `/index.html` 返回页面。当前标题是“AI 群聊”。 |
| 身份/口令 | 首次页面显示名字和访问口令输入框；`localStorage` 保存 `name`、`token`；URL 的 `?token=` 可预填口令。页面显示“口令不对”后回设置页。 |
| 收消息 | `fetch('/api/messages?since=' + cursor + '&token=' + token)` 长轮询；按 `data.messages` 追加 author/text/ts；消息 `kind=notice` 使用不同样式；自己以 `im:<name>` 判定并高亮。 |
| 断线续传 | 客户端持有整数 `cursor`；请求失败按 1、2、4、最多 5 秒退避；服务端 `LocalChatAdapter` 内存保留最近 500 条 `Broadcast`，按 seq 返回游标之后的消息。 |
| 发文本 | 表单提交 `POST /api/send`，JSON 为 `{user,text,token}`；文本为空不发；发送后清空输入框。页面只显示网络失败，不展示完整服务端错误。 |
| 基本视觉 | 暗色主题、响应式 viewport、安全区底部 padding、滚动消息区、底部输入和发送按钮；适合手机，但这不是当前目标 UI。 |

### 3.2 当前 LocalChat HTTP 接口

文件：`src/gateway/local.py:39-225`。

| 路由 | 请求/响应 | 可复用点与缺口 |
|---|---|---|
| `GET /`、`GET /index.html` | 200，`PAGE_HTML`，无 token 校验 | 可作为健康页/旧入口；Web 主应用不应继续把大页面拼成 Python 字符串。 |
| `GET /api/messages?since=<整数>&token=<口令>` | token 错返回 401；成功长轮询最多 25s，返回 `{"messages":[{"seq","author","text","kind","ts"}],"cursor":整数}` | 可复用游标与断线补齐模型；只有 gateway 广播内容，不含原始 `Message`、task、附件元数据、任务事件、成员状态。历史只在内存，服务重启不保留。 |
| `POST /api/send` | JSON body 读 token/user/text；token 错 401，非法 JSON 400，空 text 400；成功调用 `adapter.submit()` 后返回 `{"ok":true}` | `workspace` 或 `room` 字段已可选传入；但 `submit()` 只触发回调，当前 HTTP 层不能把路由失败/白名单失败作为结构化错误返回，通常先 200 再由网关异步广播 notice。无附件/task/ask 字段。 |
| 其他路径 | JSON 404 `没有这个接口` | 没有 OPTIONS/CORS、健康状态、工作区/成员/任务/控制 API。 |

网关后端另外已有可复用的桥接能力：

- `GatewayAdapter` 只要求 `start(on_message)`、`stop()`、`post(GroupPost)`；`Gateway` 负责群消息 → 路由/安全检查/入队，以及审计日志 → 群广播。
- `route_group_message()` 支持 `@成员 正文`、按房间记忆上一个目标、`im:` 远程身份；`SecurityPolicy` 做房间/用户白名单，危险指令进入 `PendingStore`，由本机 `gateway approve/reject` 处置。
- `Gateway.pump_once()` 处理已批准消息、审计 `deposit/rejected/deliver-failed` 广播；`catch_up()` 避免网关启动时把旧历史全量倒进页面。
- `WorkspaceBinder` 可按房间/工作区 slug 选择 `BusPaths`、白名单和有效成员；可为多个已登记工作区轮询广播。

这些接口适合保留为“网关/外部通道”兼容层，但 Web 版若要对齐 TUI，需要直接面向工作区状态提供更完整的查询/操作接口，不能只把 `GroupPost` 转成浏览器消息。

## 4. 可复用数据与服务接口

### 4.1 `bus`：消息、队列、审计和安全边界

代码入口：`src/bus/__init__.py`、`message.py`、`paths.py`、`queue.py`、`audit.py`、`policy.py`、`hub.py`、`ask.py`。

| 接口 | Web 可复用语义 | 注意事项 |
|---|---|---|
| `BusPaths.resolve(root=None, cwd=None)` / `for_workspace(workspace)` | 得到当前工作区隔离的 queue、processed、dead、asks、replies、log 路径 | 显式 `root` > `BUS_ROOT` > 工作区状态目录；Web 请求必须明确 workspace，不能误读仓库根 `bus/`。 |
| `Message.create()` / `Message.from_dict()` / `to_dict()` | 统一构造和校验消息；四字段 `to/from/text/ts` 冻结，可选 `id/kind/replyTo/task/attachments` | 未知字段在 `extra` 原样保留；Web API 应保持向后兼容，不另造消息格式。 |
| `Attachment` | 图片元数据（绝对路径、媒体类型、名称、宽高、大小）；队列不放二进制 | 浏览器端只能上传/引用经过服务端权限检查的附件；当前总线元数据是本机路径，不应直接暴露给浏览器。 |
| `deposit(message, paths)` / `pending(paths)` / `read_message()` | 入队、查看积压、读待处理消息 | `deposit` 原子写文件并默认记录 `deposit` 审计；Web 发言应走该入口。 |
| `Hub.drain_once()` / `Hub.run()` / `DeliveryResult` | 复用总线投递、防环、清洗、归档和结果 | TUI 通过 `BusPump` 在线程内嵌 Hub；Web 服务不能另写一套绕过 `Hub` 的投递逻辑。 |
| `AuditLog.record()` / `entries()` / `read_body()` / `record_control()` | 时间线历史、投递结果、控制审计和任务关联沟通的数据源 | 审计正文只有 80 字预览，全文另存 bodies；Web 订阅应处理日志追加、坏行跳过和轮转。 |
| `OutboundPolicy.check()` / `record()`、`receipt_for()` | 去重、限频（AI 30 秒 8 条）、收件人积压 50、正文 32KB 上限、拒收回执 | Web 不能仅在前端限频；所有写入仍经总线策略。远程 `im:` 不是 `human`。 |
| `sanitize()` / `format_for_screen()` / `format_for_injection()` | 消息展示和终端注入前清洗不可信控制序列 | 新 Web 展示出口也应清洗/转义；不能把原始 ANSI 作为 HTML。 |
| `store_ask()` / `store_reply()` / `wait_for_reply()` | ask/reply 关联 | 当前 `msg --task` 不与 ask/reply 同时使用；Web 应保留这个约束或给出明确错误。 |

### 4.2 `roster`：有效名册、生命周期、健康和收编

代码入口：`src/roster/__init__.py`、`schema.py`、`load.py`、`lifecycle.py`、`health.py`、`adopt.py`。

| 接口 | Web 可复用语义 | 交互映射 |
|---|---|---|
| `load_effective_roster(cwd=...)` | 读取当前工作区的有效成员；工作区 `members.toml`、项目 `amux.toml`、全局默认和预设按既定优先级合并 | 成员列表、补全、成员角色/命令展示的只读来源 |
| `Roster.enabled_members()` / `get(name)`、`Member` | 获取启用成员、启动参数、env、`team_id/role/leader/model/responsibility` 等元数据 | 成员详情只读；写操作需通过生命周期/名册服务，不让 Web 任意改命令字符串 |
| `Lifecycle.up/down/restart(name=None)` | 幂等拉起、关闭、重启；返回 `LifecycleResult(changed, detail)` | `/up`、`/down`、`/restart` 和按钮；终止/重启需 Web 二次确认 |
| `start_member()` / `stop_member()`、`member_env()` | 成员启动 cwd、`AGENT_NAME`、团队元数据和环境投影 | 不应在 Web 层复制启动拼装；由后端服务调用 |
| `HealthSupervisor.state()` / `watch_*()` / `reset_failed()` | dead、failed、自动重启、三次失败熔断与恢复事件 | 成员卡状态和健康告警；“重置熔断”若开放必须单独鉴权/审计 |
| `SessionAdopter.discover/adopt/forget/member_names/can_receive` | 名册外 tmux 会话发现、临时收编、本进程有效成员集合 | `/adopt`、补全和成员卡；收编状态当前不持久化，Web 重连需明确展示会话级语义 |
| `render_member_greeting()` / prompt 资源 | 启动时生成成员开场白 | Web 通常只展示角色元数据，不应重复拼装提示词 |

### 4.3 `team` 与 `work`：团队责任和任务账本

虽然 T-001 重点是 `bus/roster/team/tmuxctl`，但任务看板对齐必然需要 `work`；它依赖 `team`，因此一并列出。

| 接口 | Web 可复用语义 | 交互映射 |
|---|---|---|
| `TeamStore.list/load/init_default()` | 读取/校验 `~/.amux/teams/<id>.toml`；团队恰有一位 Leader、至少一名成员 | 团队和 Leader 只读展示；初始化团队是管理操作，不应由普通 Web 用户默认开放 |
| `load_team_binding(workspace)` / `bind_team()` | 读取/更新当前工作区绑定的团队 ID；绑定前先校验档案 | 当前团队只读；切换团队属于高影响管理操作，需明确权限和确认 |
| `roster_for_team(team)` / `activate_team()` | 将团队启动适配投影到工作区成员名册，预检全部命令后停旧成员、保存新名册并启动 | 与 TUI `/member`、生命周期/成员状态联动；Web 不应自行投影部分名册 |
| `Team` / `TeamMember` | Leader、角色、模型、effort、speed、职责、command/args/env | 任务责任、成员详情和输入默认目标的只读来源 |
| `WorkService.for_workspace()` / `snapshot()` | 从工作区绑定团队加载 `WorkSnapshot`；无绑定团队时明确不可用 | 任务看板/详情/时间线回填 |
| `WorkService.create/split/assign` | Leader 建任务、拆子任务、首次派工 | 操作；必须按当前 actor 和状态机权限执行 |
| `progress/block/add_evidence/submit` | 执行者推进、阻塞、追加证据、带证据提交 | 操作；成员不能最终结项 |
| `request_review/review_pass/review_return` | Leader 指定独立评审，评审者通过/退回 | 操作；评审者不能与执行者相同，不能代替 Leader 最终责任 |
| `reassign/takeover` | Leader 改派或留下原因、范围、原成员交付、后续验收方式后接管 | 高影响操作；必须完整收集结构化字段 |
| `accept/report` | Leader 验收并向 human 汇报，最终进入 completed | 仅 Leader 操作；需要证据、状态和子任务前置条件检查 |
| `WorkLedger.load/transact` | 只追加 `work/events.jsonl`，连续 seq + SHA-256 `prev/hash` 链，当前任务由事件重放 | Web 只应调用 `WorkService`，禁止直接写 JSONL 或维护可覆盖快照 |

### 4.4 `tmuxctl`：终端输出、镜像和控制

代码入口：`src/tmuxctl/__init__.py`、`client.py`、`snapshot.py`、`output.py`、`inject.py`、`activity.py`、`process.py`、`lifecycle.py`。

| 接口 | Web 可复用语义 | 风险/难点 |
|---|---|---|
| `Tmux.has_session/list_panes/capture_pane/capture_with_cursor` | 会话存在、pane 信息、ANSI 当前画面、光标位置 | 必须在服务端执行；不能把 tmux 命令参数直接暴露给浏览器 |
| `PaneSnapshotter.capture(target, color, start)` | 合并刷新窗口、读取带颜色快照和回滚起点 | 适合先做 WebSocket/SSE 快照流；需限流、取消不可见订阅、处理工作区/成员隔离 |
| `PaneOutputStream` / `subscribe_pane` | control mode/FIFO 输出流，驱动活动状态和实时镜像 | 长连接资源回收、断线重连和 tmux server 消失是高难点；原始输出不能作为语义日志解析 |
| `ActivityTracker` / `ActivityMonitor` | 输出、工作标记、存活状态投影为 activity snapshot | Web 可轮询/订阅状态快照，避免让每个浏览器独占底层流 |
| `KeyInjector.deliver/text/ensure_submitted/key` | 安全注入文本、分离 Enter、确认 composer 已提交、白名单按键 | 只能复用注入策略，不能用 Web 请求线程直接 `send-keys` 绕过忙碌检测/确认 |
| `ProcessController.interrupt/terminate/kill/kill_session/process_tree` | 打断、终止、进程树和会话控制 | 所有操作要权限、确认、审计；kill 类能力不等价于普通按钮 |
| `CrashMonitor.wait/respawn` | 监听 pane/session 崩溃并为 HealthSupervisor 提供恢复 | 后端应集中监听，不能每个浏览器页面各启一个 monitor |
| `Tmux.fit_window/release_window_size/command_argv` | 镜像时适配窗口，桌面 attach 时恢复尺寸 | 浏览器没有等价的 tmux attach；需决定“打开终端”是只读镜像、远程直连，还是跳转本机客户端 |

## 5. Web 版对齐时的边界与实现建议

### 5.1 建议的后端投影

Web 层可以把现有接口投影成以下几类资源；名称是建议，不是已冻结 API：

1. `workspace`: 当前工作区、项目根、团队绑定、成员命名空间和能力/权限摘要。
2. `team` / `members`: Leader、成员职责/模型/effort/speed、有效名册、状态、队列数、最后活动、健康告警。
3. `tasks` / `tasks/{id}`: `WorkSnapshot` 任务、证据、事件流、关联沟通；写操作对应 `WorkService` 的类型化动作。
4. `conversation`: 审计日志投影后的时间线条目，带 category、outcome、reason、task、attachment count；通过 SSE 或 WebSocket 推送新增条目，历史用游标/分页。
5. `messages`: 发言、@ 路由、task 关联、ask/reply；服务端走 `Message` + `deposit`，不从浏览器直接写 queue 文件。
6. `members/{name}/screen`: 当前 ANSI 快照、光标/网格尺寸、历史偏移；只读镜像与滚动游标分开，避免把 ANSI 原文当 HTML。
7. `members/{name}/control`: 受限的生命周期、打断、按键、直连文本和接管请求；统一后端串行队列 + 审计 + actor 权限。
8. `attachments`: 浏览器上传到工作区状态目录，服务端生成/校验 `Attachment` 元数据；下载/展示必须做路径越界和权限检查。

### 5.2 与现有 page 的差异清单

- 认证：现有 token 是共享口令，名字由客户端自报；对齐桌面 Web 的成员控制和任务验收前，至少需要区分 human actor、会话身份、只读/操作权限，并保留现有网关白名单和危险指令二次确认。
- 实时性：现有长轮询适合少量聊天广播，但不适合 ≤100ms 的终端镜像和控制回显。可以保留 `/api/messages` 兼容入口，新增针对状态/镜像/事件的 SSE/WebSocket 或有界轮询。
- 错误：当前 `POST /api/send` 只返回成功/基础 HTTP 错误，路由、白名单、限频和危险挂起结果异步广播；Web 对齐需要结构化返回 `accepted/rejected/pending` 及原因，同时仍保留审计和总线回执。
- 状态：当前 gateway 只在内存保存 500 条广播，重启后 cursor 不能恢复历史；任务和工作对话必须从 `work/events.jsonl`、`bus/log.jsonl` 做服务端分页/游标读取。
- 数据模型：当前 `Broadcast` 只有 seq/author/text/kind/ts；应扩展为浏览器安全的展示 DTO，保留 task/category/outcome/reason/attachment 摘要，不泄露本机附件绝对路径和未经清洗的终端控制序列。
- 多工作区：已有 `WorkspaceBinder`，但 Web 会话必须把 workspace 绑定到认证上下文，不能允许客户端仅靠 body 中的 `workspace` 任意跨区投递或读取。
- 图片：TUI 通过系统剪贴板，浏览器应改为受限 multipart/分块上传；上传后再生成当前冻结的 `Attachment` 元数据，不能把 base64 塞进总线消息。

### 5.3 高难项单列

1. **tmux 窗格实时画面**：ANSI → 浏览器终端渲染、颜色/宽字符/光标/滚动区、刷新节流、断线续传和多个观看者共享订阅。
2. **点击直连与按键透传**：浏览器坐标到 tmux 网格坐标的映射、只允许识别的 composer、文本/按键/Enter 单一串行队列、禁止回滚态输入，以及审计/权限。
3. **attach 接管**：浏览器无法自然执行本机 `tmux attach-session`；需要明确桌面 Web 的替代交互（只读镜像、服务端伪终端、或唤起本机客户端），不能宣称已有 F8 语义已经对齐。
4. **多工作区和多浏览器 actor**：读写隔离、同成员并发操作、控制权冲突、断线后的操作幂等和审计 actor 归属。
5. **高影响任务操作**：Web 表单需完整覆盖接管四字段、Leader-only 验收/汇报、评审冲突、状态转换错误和账本损坏提示，不能用一个自由文本“完成”按钮替代 `WorkService`。

### 5.4 WEB-009 收口清单（T-023 终验，2026-08-30）

1. **(a) T-017：`control.timeline_snapshot_view`**——已收口。T-022 接入共享 `projector`/`TimelineCache`，`tests/test_control_plane.py` 有同进程三处 seq 同源用例；`main` 已验证，无遗留。
2. **(b) T-013：显式 `websockets` 依赖**——已收口，结论为**不改**：`uvicorn[standard]` 已含 `websockets`(真实浏览器 WS 升级需要，纯 ASGI-server 不带；WEB-004/T-013 当初就是因为缺它导致 `/api/v1/stream` 在真实服务器下 404、`TestClient` 内存传输掩盖了问题，才把依赖从 `uvicorn` 换成 `uvicorn[standard]`)，拆出显式依赖只省安装体积、不省运行时行为，而每次改动都要求重跑真实握手冒烟+同步 `tests/test_release_package.py` 断言，收益不足以覆盖这个持续维护成本。维持现状，不改 `pyproject.toml`。
3. **(c) 附件存储无配额/清理策略**——已收口。`ContentAddressedImageStore` 新增 `max_store_bytes`(默认 500MB)总容量上限，达到上限拒绝新上传(`ImageAttachmentError`，同现有单文件超限一样映射到 `invalid-request`/400)；不做自动淘汰——内容寻址文件可能仍被历史消息引用，删除会让回看时间线里的图片永久 404，安全的淘汰需要先知道哪些附件还被引用，这是跨切面的更大改动，作为已知限制记录，不在本次范围内实现。`tests/test_control_attachments.py` 覆盖到达上限拒绝新内容、同内容去重不占二次配额。
4. **(d) 写端点与 WS 票据的 §6.3 合规复查**——已收口，无新增未合规项。T-019/opus 已对 WEB-008 落地时的 8 个写路由与 `attach_token`/`direct_token`(`ConfirmationStore`：CSPRNG ≥128 位熵、绑定 actor/member/action、单次消费、30s 过期)做过完整审计；T-023 在同一 `main` 提交(`e70eea3`)上复核，写路由数量与挂载的 `require_write_session` 依赖均未变化，无需重新走一遍审计，直接引用 T-019 证据。

## 6. 调研依据与验证

本清单基于以下仓库内权威/实现文件阅读整理：

- 产品与架构：`docs/product/product.md`、`docs/architecture/architecture.md`、`docs/goals/console.md`、`docs/goals/gateway.md`。
- TUI：`src/console/app.py`、`commands.py`、`compose.py`、`widgets.py`、`timeline.py`、`workview.py`、`mirror.py`、`control.py`、`help.py`。
- 网关：`src/gateway/page.py`、`local.py`、`base.py`、`router.py`、`security.py`、`workspaces.py`、`config.py`。
- 复用层：`src/bus/`、`src/roster/`、`src/team/`、`src/work/`、`src/tmuxctl/` 的公开模型与方法。

实际运行的调研命令：

```text
uv run amux task show T-001
uv run amux task progress T-001 '范围已更新为桌面 Web 与 TUI 并列、对齐 TUI 全部功能；inventory 将逐项记录来源(bus/roster/team/tmuxctl)与交互类型，并标记高难项。'
rg -n '^class |^def |^    (async )?def |BINDINGS|key=|action_|on_|/up|/down|/restart|/adopt|/mute|/help|F5|F6|F7|F8|Ctrl\\+V|clipboard|task' src/console --glob '*.py'
rg -n '^class |^def |^    (async )?def ' src/bus src/roster src/team src/work src/tmuxctl --glob '*.py'
```

本次只修改文档与 `src/console/cli.py` 的 CLI 帮助文案，未修改 Web/TUI 运行时实现或测试；验证命令和结果记录在 WEB-009 前半的 Goal 证据中。
