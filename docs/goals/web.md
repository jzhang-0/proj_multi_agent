# Web 控制台(WEB)

在 TUI 之外提供桌面浏览器控制台，与 TUI **并列运行、全功能对齐**。功能基线是 [Web 版功能盘点 §2](../web/inventory.md#2-tui-画面与操作总表) 的逐项清单；架构依据是 [Web 版架构方案比较](../web/architecture-options.md)(推荐路线 B)。

## 已定的设计(2026-08-30 human 拍板)

1. Web 视图目标是对齐 TUI 现有全部功能；TUI 保留并与 Web 并列。手机 UI 暂缓，现有 gateway 手机群聊页继续承担低权限远程群聊，不升级为控制台。
2. 技术路线 B：同仓库、同 wheel 的 FastAPI/ASGI 后端 + TypeScript SPA。TypeScript/Node 只作构建依赖，编译产物打进 wheel；运行时不依赖 Node、源码路径或 CDN。前端框架在 WEB-005 从 Preact 或轻量原生组件中选一项。
3. FastAPI/ASGI server 等 Web 依赖**默认随 `amux-team` 安装**，不做 extra。
4. 首版只监听 `127.0.0.1`；局域网/公网访问、TLS、登录另立 Goal。
5. 多端控制：多人可看；每成员同一时间仅一个交互租约(resize/直连/接管)，显式抢占；每工作区仅一个 Hub 投递者。
6. 浏览器完整接管(F8 对等)= 后端专用 PTY 固定运行 `tmux attach-session`，经 WebSocket 桥接到浏览器终端组件；不开放任意 shell。

## 不可改写的契约

- `bus` 四字段消息与文件队列、`work/events.jsonl` 哈希链、tmux 作为唯一终端通道、`workspace/team/roster` 公开模型——Web 只调用，不建第二套状态库(架构 §3)。
- 浏览器传来的 actor、路径、tmux target、任务权限声明一律不可信；对浏览器输出的文本必须结构化编码或安全转义。
- 所有控制动作继续写 `AuditLog.record_control`；租约不绕过领域权限。

## Goal

执行顺序：WEB-001 → WEB-002 → WEB-003 → WEB-004 为主线，之后 WEB-005～008 可按前置并行，WEB-009 收口。每个 Goal 单独可合入 `main` 且不破坏既有 TUI。Goal 中引用的「」条目名即 inventory §2 的完整枚举，不得删减。

- [x] **WEB-001** — Web/TUI 共用控制面与读模型：新建不依赖 Textual/HTTP 的控制面层(建议 `src/control/`)，下沉 `console.timeline` 的审计+任务事件→工作对话读模型投影、`console.control.MemberController` 的成员控制与审计编排、`console.app` 中的成员状态汇总，以及 `console.mirror.terminal_input_rows` 等纯文本识别函数；返回 dataclass/枚举等可 JSON 序列化的 DTO，不返回 Rich `Text`/Textual widget/HTML。TUI 改为调用该层。覆盖「左栏任务摘要」「左栏成员卡片」「中栏任务详情」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」的数据语义，以及「成员打断」「成员终止」「成员重启」的控制/审计语义。控制面模块禁止 import `console`(以测试钉住)；现有 console 测试与视觉基线不变。
  - 验证(Sol，2026-08-30):`uv run ruff check .` 通过；`uv run pytest tests/test_control_plane.py tests/test_console_timeline.py tests/test_console_members.py tests/test_console_control.py tests/test_console_mirror.py tests/test_console_health.py tests/test_console_work.py tests/test_control_lease.py tests/test_release_package.py -q` 为 81 passed。`uv run python -m qa.visual --goal WEB-001 --fixture task-board` 分别以 120x30、80x24 实际截取并逐项查看，任务层级、成员卡、证据/事件流/关联沟通及中英文窄屏换行均可辨认，既有 baseline 文件未改写。
  - 证据:`tests/test_control_plane.py` 以 AST 静态扫描禁止 `control` import console/Rich/Textual/HTTP，并对成员、任务、时间线、健康、控制反馈和词表 DTO 执行 `json.dumps(asdict(...))` 与禁用类型检查；同文件钉住 deposit+deliver 与 work 事件混排时关联沟通 `timeline_seq` 同源，以及超过 `HISTORY_LIMIT` 后回填与 snapshot 保持全量稳定 seq；`tests/baseline/web-001-task-board-120x30.txt`、`tests/baseline/web-001-task-board-min-80x24.txt`（同名 `.ansi` 保留颜色）；实现提交 `e403a93`、`ddf0026`，main 合入 `4c8ffb0`，评审退回修复提交 `6d47171`。
  - 前置:TEAM-002、CON-019。

- [x] **WEB-002** — 多前端运行时协调：每工作区单一 Hub 投递租约(其他进程只观察，持锁者退出后可恢复)；每成员单一交互租约，覆盖 resize/直连/接管，显式抢占；崩溃/断线自动释放。约束「终端窗口适配」「成员画面点击直连」「直连文本输入」「直连编辑/方向键」「全屏接管 / attach」「退出」：两个 TUI/测试进程并列时不得重复投递、互相 resize 或交错键入；关闭一个前端不得关闭成员会话。与 Web 框架无关，TUI 先接入。
  - 验证(Grok，2026-08-30):`uv run ruff check src/console/app.py src/control/lease.py src/control/__init__.py tests/test_console_lease.py tests/test_control_lease.py` 通过；`uv run pytest tests/test_console_lease.py tests/test_control_lease.py tests/test_console_app.py tests/test_console_control.py tests/test_console_mirror.py tests/test_console_keyboard.py tests/test_console_health.py tests/test_workspace_console.py tests/test_console_compose.py tests/test_console_members.py tests/test_console_work.py tests/test_control_plane.py -q` 为 95 passed。`uv run python -m qa.visual --goal WEB-002 --fixture task-board` 以 120x30、80x24 实际截取；`--scene member-view --fixture controls --keys Down` 120x30 实际截取。
  - 证据:`src/console/app.py` 启动时实例化 `HubDeliveryLease`/`MemberLeaseManager` 并接入 `BusPump`、`_fit_member_window`、直连键入与 F8 接管（`force=True`）；退出与切走成员只释放租约/`release_window_size`，不关会话。`tests/test_console_lease.py`；`src/control/lease.py` 的 `leases_dir` 与 `release` 在目录不存在时幂等。截取物 `tests/baseline/web-002-task-board-120x30.txt`、`tests/baseline/web-002-task-board-min-80x24.txt`、`tests/baseline/web-002-member-view-120x30.txt`（同名 `.ansi` 保留颜色）：120x30 任务层级/证据/事件流/成员卡可辨认；80x24 三栏仍可用且中文换行；成员画面直连提示与 `q`/`f8` 仍在，单前端无租约拒收噪声。实现提交 `66d56ac`，main 合入 `e494e41`。
  - 前置:WEB-001。

- [x] **WEB-003** — Web 后端骨架与一致 snapshot API：`amux web` 入口，FastAPI/ASGI，默认只监听 `127.0.0.1`，本机认证会话；提供 workspace/team/work/timeline/member snapshot(带 `epoch/revision`)，只调用 WEB-001 控制面。接口覆盖「顶部 Header / 工作区副标题」「左栏任务摘要」「左栏工作对话记录卡」「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」。wheel 内带最小静态健康页；`qa.release` 源码外安装后可启动。Web 依赖加入默认依赖。
  - 验证(Sonnet/Sol，2026-08-30):`uv run ruff check .` 全过；`uv run pytest tests/test_web_snapshots.py tests/test_web_auth.py tests/test_control_plane.py tests/test_console_timeline.py tests/test_console_work.py tests/test_work_ledger.py tests/test_release_package.py tests/test_control_lease.py -q` 为 72 passed；本次评审退回修复按指定范围运行 `uv run pytest tests/test_web_snapshots.py tests/test_web_auth.py tests/test_control_plane.py -q` 为 26 passed；`uv build --wheel` 实测 wheel 内确认 `web/{__init__,app,auth,cli,context,errors,snapshots,state}.py`、`web/static/health.html` 全部路径存在。
  - 证据:`src/web/app.py` 接入 `/api/v1/{session,bootstrap,vocabulary,workspace,team,work,work/tasks/{id},timeline,timeline/{seq}/body,members,health}`，全部只读、经 `require_session` 鉴权与 `require_runtime` 就绪检查(`APIRouter` 统一依赖，覆盖全部子路由，非逐路由手写) + 既有 Host 校验中间件，响应带 `Cache-Control: no-store`(`vocabulary` 除外)与 `application/json; charset=utf-8`；`src/web/context.py` 按 §1 规则只读解析工作区/团队/总线/tmux(直接构造 `WorkService`/`AuditLog`/`Tmux`，与 `console/app.py` 既有取资源方式一致，不触发 resize/send_keys)；`src/web/state.py` 实现 §3 epoch/revision 语义(指纹变化才 bump)与单槽时间线投影缓存；`src/web/snapshots.py` 是 §4.1-4.11 DTO 组装，投影/聚合一律经 `control.tasks`/`control.timeline`/`control.members`/`control.health`/`control.vocabulary`，不自行重算业务口径；错误按 §2.4 映射(`workspace-unregistered`/`team-unbound`/`not-found`/`invalid-request`/`work-unavailable`)。复审(fable)指出 `task_detail_view` 每次全量重建时间线(2 万行审计约 0.28s)是 `/work/tasks/{id}` 热点，已给 `control/tasks.py` 的 `task_communications`/`task_detail_view` 加可选 `timeline` 参数复用调用方已投影结果(向后兼容，既有调用不变；`seq` 仍是 `history_from_entries` 同一基准，未改动其算法，与 T-003 `6d47171` 钉住的语义一致)；评审(opus)预检发现 `timeline_snapshot_view` 上同名参数未被任何调用方使用，已删除(仅保留 `control/tasks.py` 那一处实际生效)。`tests/test_web_snapshots.py` 轻量覆盖每端点一条正常路径 + 未登记/未绑定/404/400/空账本边界。分支 `web-003-sonnet` 合入 main(`8175370`)，评审前修正合入 `53f0d74`。
  - 评审(opus)第一轮退回两点，均已修:①`/api/v1/members` 每请求新建 `MemberStatusService`，`working`/`stuck` 不可达、`silent_for` 恒 `None`(实测 web 路径 idle/None，TUI 路径 working/2.7e-06)——改为 `web/app.py` 的 `lifespan` 起常驻 `MemberStatusService` 并调度 `run()`(对齐 `console.app` 接线)，`members_dto`/`bootstrap_dto` 改为读注入的常驻服务，用 `track()` 跟上名册变化；`build_context()` 新增可选 `tmux` 参数复用 lifespan 探测好的客户端。②错误响应缺 `charset=utf-8`——`ApiJSONResponse` 挪到 `web/errors.py`，`app.py` 的 `_error()` 与统一异常处理器都改用它。新增测试:喂输出后 `/api/v1/members` 读到的状态与 `member_status.snapshot()`(TUI 等价路径)一致且 `silent_for` 为数字；tmux 不可用时 lifespan 不阻塞启动、`/members`/`/health` 仍 200；401(坏 Host)与 404 各钉一条 charset 断言。修复分支 `web-003-fix-sonnet` 合入 main(`046cd35`)。
  - 后续复审(opus)退回两点，均已修:①tmux 不可用时没有后台监视任务纠正 `ActivityTracker` 的默认 alive——lifespan 初始化及 `members_dto.track()` 新增成员后均显式 `set_alive(False)`，`/members` 对所有成员返回 `state=dead/alive=false`；②未运行 lifespan 时 `app.state.tmux/member_status` 未初始化——API router 在认证后统一通过运行时依赖返回 §2.4 `work-unavailable/503`，不再冒 `AttributeError` 500。`tests/test_web_snapshots.py` 同时钉住初始/新增成员 dead 状态、认证优先级及 workspace/members 两类未就绪端点；实现提交 `30af5c2`，main 合入 `65bed37`。
  - 前置:WEB-001。

- [ ] **WEB-004** — versioned 实时事件流：WebSocket 推送 work/bus/team/roster/member 的 invalidation 或 delta(watchfiles 或领域动作触发)；`epoch/revision` 断档时客户端全量 resync；每客户端有界队列，慢客户端不积压。实时验收针对「左栏工作对话记录卡」未读数、「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」。以测试客户端验证，不依赖 SPA。
  - 前置:WEB-003。

- [ ] **WEB-005** — 桌面 SPA 只读与导航闭环：TypeScript SPA(Preact 或轻量原生组件，此处定案)，构建产物进 Python 包。实现「顶部 Header / 工作区副标题」「左栏任务摘要」「左栏工作对话记录卡」「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「时间线分类筛选」「时间线滚动」「`/workspace`」「`/task [ID]` / F3」「`/help` / ? / F1」「深浅主题 / T」「健康告警」「成员状态实时更新」「退出」。`/workspace` 与 `/task` 可用等价 Web 导航。组件测试 + Playwright 截图视觉自验证。
  - 前置:WEB-004。

- [ ] **WEB-006** — 消息、ask/reply 与浏览器附件：实现「工作对话输入框」「@ 成员补全」「发送 ask/reply」「图片粘贴/发送」「待发图片撤销」。浏览器上传写入现有内容寻址附件存储，响应/下载不泄露绝对路径；消息走 `Message.create()` + `deposit()`；默认 Leader、选中 task、上次对象、ask/task 互斥语义与 TUI 一致；actor 来自认证上下文，不接受客户端自报。
  - 前置:WEB-005。

- [ ] **WEB-007** — tmux 镜像、回滚与受限直连：实现「成员终端镜像」「成员终端回滚」「终端窗口适配」「成员画面点击直连」「直连文本输入」「直连编辑/方向键」。完整帧 WebSocket，≤10 Hz 合并/背压，ANSI 经随包终端组件安全渲染(禁 `innerHTML`/CDN)，canonical size 由租约持有者决定；浏览器行号须经服务端最新帧再次确认 composer；输入经 WEB-002 租约与 `tmuxctl` 串行。覆盖 TUI/Web 同时观看、抢占、回滚态拒绝输入、断线释放测试。
  - 前置:WEB-002、WEB-005。

- [ ] **WEB-008** — 成员管理、生命周期与完整接管：实现「成员打断」「成员终止」「成员重启」「全屏接管 / attach」「`/up`」「`/down`」「`/restart`」「`/adopt`」「`/mute`」「`/member add/rm/list`」。危险动作二次确认与审计；完整接管为 PTY bridge 固定 `tmux attach-session -t =<会话>`，收口在 `tmuxctl`，断线 detach+关 PTY+释放租约+审计；`/adopt` 明示进程级临时状态；`/mute` 走 Hub 策略。真实 tmux + 浏览器验证。
  - 前置:WEB-007。

- [ ] **WEB-009** — 全功能矩阵与发布收口：逐行勾验 inventory §2 全部条目(未完成项如实保留)；更新产品、架构、README、CLI 帮助与安全说明；前端产物进 sdist/wheel；`qa.release` 源码外联网安装并启动 Web，验证不依赖 Node/源码路径/CDN；复查「退出」只关 Web 会话不关成员。
  - 前置:WEB-006、WEB-008。
