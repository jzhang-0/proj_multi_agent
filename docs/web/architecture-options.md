# Web 版架构方案比较

状态：提案，供 human 拍板；不改变现有产品与架构权威文档。

## 1. 目标与约束

human 已明确：Web 视图最终要与 TUI **全功能对齐**，TUI 继续保留并与 Web 并列；首版按桌面浏览器设计，手机 UI 暂缓。这里的“全功能”至少包括：

- 工作区切换、团队与成员状态、任务看板/详情/证据/责任事件流；
- 工作对话记录、分类筛选、任务关联消息、发送消息与图片附件；
- 成员终端画面、回滚区、可确认输入区的点击直连、按键透传；
- 打断、终止、重启和完整接管，以及相同的确认与审计语义。

功能基线以 [Web 版功能盘点 §2](inventory.md#2-tui-画面与操作总表) 的逐项清单为准；下文 Goal 直接引用其中的条目名，避免用“主要功能”等不可验收的概括替代完整清单。

以下现有契约不能因 Web 改写：

1. `bus` 的四字段消息契约和文件队列继续是通信事实来源，新字段只能可选扩展。
2. `work/events.jsonl` 继续是只追加、带哈希链的任务事实来源；写操作必须走 `WorkService`，不能由 HTTP handler 直接改文件。
3. tmux 继续是成员终端的唯一控制通道；Web 层不能直接拼 tmux 命令，必须走 `tmuxctl`。
4. 工作区、团队、名册分别通过 `workspace`、`team`、`roster` 的公开模型与服务读取或变更，不能为 Web 建第二套状态库。
5. 所有对浏览器输出的文本都要结构化编码或安全转义；浏览器传来的 actor、路径、tmux target 和任务权限声明均不可信。

当前 `src/gateway/local.py` 已有 `ThreadingHTTPServer`、单文件页面、token 和长轮询，但它的产品语义是“远程 IM 代投”：发件人带 `im:`、危险指令需本机二次确认。完整 Web 控制台是另一条权限边界。即便选择路线 A，也只能复用 HTTP 基础设施，不能把本机 Web 操作伪装成 `Gateway.on_group_message`。

## 2. 先确定共用边界

三条路线都不应让第二套 UI 复制领域规则。建议先形成一个不依赖 Textual、HTTP 或浏览器的“控制面”层，TUI 和 Web 都调用它：

```text
TUI / Web API / CLI
        │
        ▼
control plane（用例、读模型、权限上下文、事件通知）
        │
        ├── workspace / team / roster
        ├── work（WorkService / WorkSnapshot）
        ├── bus（deposit / AuditLog / Hub）
        └── tmuxctl（snapshot / lifecycle / input / process control）
```

可直接复用的状态模型包括 `Workspace`/`Store`、`Team`/`TeamStore`、`WorkService`/`WorkSnapshot`、`AuditLog`、`BusPaths`、`PaneSnapshotter`、`Lifecycle` 和 `tmuxctl` 的控制器。当前仍与 Textual 绑定、需要下沉的部分主要有：

- `console.timeline` 中“审计日志 + 任务事件 → 工作对话读模型”的投影；
- `console.control.MemberController` 中成员控制与审计编排；
- `console.app` 中工作区切换、成员选择、窗口尺寸、实时输入队列和刷新策略；
- `console.workview`、`console.mirror` 中混合了领域判断与 Textual 渲染的逻辑。

下沉后应返回普通 dataclass/枚举/JSON 可序列化 DTO，不返回 Rich `Text`、Textual widget 或 HTML。TUI 保留自己的渲染，SPA 也保留自己的渲染；两端共享的是状态、用例、权限和事件语义，而不是像素级 UI 代码。

还需要解决并列运行的所有权问题：当前 TUI 会内嵌 `BusPump`，多个 TUI/Web 进程同时跑可能重复消费队列；多个视图也可能反复争抢同一个 tmux window 的尺寸或同时向一个 composer 键入内容。无论选哪条路线，都应增加：

- 每工作区单一投递者租约，其他进程只观察；持锁者退出后可恢复；
- 每成员单一交互控制租约，多视图可读，只有租约持有者可 resize/直连/接管；
- 所有控制动作继续写 `AuditLog.record_control`，租约不能绕过领域权限。

## 3. tmux 窗格在浏览器中的呈现

### 3.1 普通镜像与回滚

推荐复用 `PaneSnapshotter.capture(color=True, start=...)`，以最多 10 Hz 获取 `capture-pane -p -e` 的**完整帧**，而不是把 `PaneOutputStream` 的原始增量输出当作当前画面。原始输出只适合判断活跃或唤醒刷新；它包含 ANSI 重绘，断线、丢帧或中途订阅后不能可靠重建屏幕。

服务端给每帧带 `workspace`、`member`、`frame_seq`、列行数和回滚偏移。新帧覆盖旧帧；慢客户端只保留最新帧，不能积压过时画面。浏览器端有两种安全渲染方式：

- 把 ANSI SGR 解析成类型化的行/文本 span，再用 DOM 文本节点渲染；或
- 把清屏/归位序列加在完整 ANSI 帧前，交给随 wheel 打包的终端渲染组件，禁止 CDN 和 `innerHTML`。

第二种对 256 色、Unicode 宽度和未来完整接管的复用更好，推荐采用。浏览器滚动仍改变服务端 capture 的 `start`，读的是 tmux 自己的回滚区，不另造 Web 历史。

窗口尺寸不能由每个浏览器 tab 各自决定。建议由当前交互租约持有者决定 tmux window 的 canonical rows/cols；其他 TUI/Web 视图按比例留白或裁切。否则 TUI 与 Web 并列打开时会互相触发 `resize-window`，成员 CLI 将持续重排。

### 3.2 点击直连与完整接管

普通点击直连可继续复用 `terminal_input_rows` 的 Claude/Codex composer 识别规则，但应把纯文本识别函数下沉到中立模块。浏览器只上送“点击的行号”和后续键盘事件，服务端必须再次用最新帧确认该行仍是可输入区；按键只允许走现有白名单和 `tmuxctl` 串行队列。

Web 中的 F8“完整接管”不能简单调用现有 `MemberController.takeover()`：后者把本机终端交给 `tmux attach`，浏览器没有可继承的本机 TTY。要达到功能对齐，需要后端为该动作创建一个专用 PTY，只启动固定的 `tmux attach-session -t =<已解析会话>`，再把 PTY 字节通过 WebSocket 接到浏览器终端组件。该实现仍需收口在 `tmuxctl`，不得提供任意 shell/命令参数。断线时必须 detach、关闭 PTY、释放交互租约并记审计。

## 4. 实时状态模型

任务、消息、名册状态和终端画面不应混用同一刷新策略：

| 数据 | 权威来源 | 建议推送方式 | 断线恢复 |
|---|---|---|---|
| 任务与责任事件 | `WorkService.snapshot()` / `work/events.jsonl` | 文件事件触发 `work.changed`，客户端重取或接收小 delta | 按 `WorkEvent.seq` 补齐；校验失败直接报错，不降级成缓存 |
| 消息与控制审计 | `AuditLog` / `bus/log*.jsonl` | `bus.changed` + 合并后的 timeline delta | 重取最近窗口并按 message id/审计位置去重 |
| 团队、名册、工作区 | `TeamStore`、绑定、有效 roster、`Store` | 配置文件变化后发 snapshot/invalidate | 全量重取；规模很小 |
| 成员健康 | `MemberStatusService`/tmux 探测 | 状态变化推送，低频心跳 | 全量重取 |
| 活跃成员画面 | `PaneSnapshotter` | 独立的有界 WebSocket 帧流，最多 10 Hz | 立即发最新完整帧 |

推荐协议是“HTTP 获取一致 snapshot + WebSocket 发 versioned delta/invalidate”。连接建立时先拿 snapshot 及 `epoch/revision`，再订阅；若 revision 断档、服务重启导致 epoch 改变，客户端丢弃局部缓存并重取 snapshot。这样不需要为了 Web 把文件事实来源迁到数据库，也不会把进程内 WebSocket 队列误当成可靠日志。

## 5. 路线比较

### A. 扩展现有 gateway：标准库 HTTP + 单页

形态：扩展 `LocalChatAdapter` 一类的标准库服务器，增加任务、团队、名册、成员控制和终端帧接口；页面继续由 Python 包内单文件 HTML/CSS/JS 提供。状态推送沿用长轮询，终端输入用 POST。

优点：

- Python 运行依赖几乎不增加，现有 token、长轮询、断线游标和 stdlib HTTP 测试有直接起点；
- 没有 Node 构建步骤，单文件资源进入 wheel 最简单；
- 对“只读任务看板 + 消息发送”的首个演示成本最低。

主要问题：

- 10 Hz 终端帧、按键输入和 PTY 接管都是高频双向通道。用 `ThreadingHTTPServer` + 长轮询/POST 实现会产生大量请求、线程和自制流控；自己实现 WebSocket 又会抵消“只用标准库”的主要价值。
- 一个全功能桌面控制台塞进单个字符串页面后，路由、状态管理、组件测试、无障碍和错误恢复会快速失控；现有 `PAGE_HTML` 的测试只证明群聊页行为，不能覆盖完整 SPA。
- 标准库 handler 没有成熟的请求模型、依赖注入、WebSocket、统一错误处理或自动 API schema；权限检查容易散落在分支中。
- 仍必须抽公共控制面，不能直接从 handler 读 `events.jsonl`、`members.toml` 或拼 tmux 命令。

实时推送：任务/消息可继续长轮询；终端帧与接管若不用 WebSocket，只能拆成下行长轮询和上行 POST，虽可做但复杂度与延迟余量都差。现有 `wait_for()` 每 100 ms 检查一次列表，也不适合按多个成员/客户端直接放大。

测试：Python handler 可沿用 `urllib` + pytest；完整页面仍需浏览器 E2E。单文件脚本缺少自然的组件单测边界，最终并不会省掉 Playwright 一类真实浏览器测试。

wheel/PyPI：最轻；继续把页面作为 Python 包资源即可，wheel 保持纯 Python。但资源越大，字符串内嵌越难做内容哈希、缓存策略和独立前端测试。

结论：适合只读/轻交互原型，不适合已拍板的全功能长期目标。

### B. 独立 Python Web 后端 + SPA

形态：在同一仓库和发行包内新增独立 Web 进程/入口；后端建议 FastAPI/ASGI，前端为 TypeScript SPA。这里的“独立”是进程与传输层独立，不是另建业务后端或数据库。FastAPI 官方直接支持 WebSocket、静态文件与 TestClient；它基于 Starlette/Pydantic，标准安装通常再带 Uvicorn 等运行依赖（[WebSocket 文档](https://fastapi.tiangolo.com/advanced/websockets/)、[测试文档](https://fastapi.tiangolo.com/tutorial/testing/)、[依赖说明](https://pypi.org/project/fastapi/)）。

优点：

- HTTP snapshot、WebSocket 状态流、终端帧和 PTY 接管都有清晰的一等协议边界；连接取消、背压、鉴权和错误模型可集中处理。
- SPA 能自然拆分任务、时间线、名册、终端和确认弹窗；浏览器剪贴板、文件上传、键盘捕获和终端渲染均可按 Web 平台能力实现。
- API 只调用公共控制面，TUI 同时改用该层，因此状态机、权限与审计只有一套；两套 UI 只分别维护视图代码。
- 后续若恢复手机 UI，可在同一 API 上增加响应式布局，不必重做领域接口。

代价与风险：

- 增加 FastAPI/Starlette/Pydantic/ASGI server/WebSocket 依赖，以及前端包管理和构建链；Python 与 TypeScript 都要维护。
- 需要定义稳定 DTO、事件协议和兼容策略；接口不是公网 API，但至少要在一个 minor 版本内向后兼容，避免旧页面缓存连上新后端即失效。
- 构建流水线必须先产出前端静态目录，再构建 sdist/wheel；源码外安装不能依赖 Node、仓库路径或 CDN。

实时推送：按第 4 节采用 snapshot + WebSocket；终端使用独立、只保留最新帧的通道；接管使用独立 PTY WebSocket。不要把所有事件塞进一个无界广播队列。

测试：

- 共享控制面继续用 pytest 和临时 `AMUX_HOME`/bus；
- API 用 FastAPI TestClient/httpx 覆盖权限、错误和 WebSocket 重连；
- SPA 用组件测试，关键流程用 Playwright；
- tmux 画面、输入和接管用隔离 tmux socket + 真浏览器测试，验证断线释放租约；
- 发布测试从源码外安装 wheel，检查 Web 入口、后端依赖、静态资源哈希和无 CDN 运行。

wheel/PyPI：可保持单个 `amux-team` 纯 Python wheel 加静态资源；最终用户不需要 Node。需在 Hatch 配置中显式包含构建产物，并在 `qa.release` 检查 wheel/sdist 资源。Web 依赖是默认依赖还是 `amux-team[web]` extra 需 human 拍板；若 Web 是一等并列入口，默认依赖可减少“命令存在但不能运行”的割裂，extra 则能保住 TUI 的轻安装。

结论：前期工程量最大，但最符合全功能、双界面长期并列和未来扩展，推荐作为正式路线。

### C. `textual-serve` 直接把 TUI 搬到浏览器

形态：用 `textual-serve` 启动现有 `amux` Textual 应用。其官方实现会在浏览器访问时启动一个应用子进程，并通过 WebSocket 使用自定义协议通信；它不是把 shell 直接暴露给浏览器（[官方 README](https://github.com/Textualize/textual-serve)）。当前项目元数据列出的依赖包括 `aiohttp`、`aiohttp-jinja2`、`jinja2`、`rich` 和 `textual`（[官方 pyproject](https://github.com/Textualize/textual-serve/blob/main/pyproject.toml)）。

优点：

- 任务、时间线和普通 Textual 交互几乎可以原样呈现，视觉与按键行为最接近现有 TUI；
- 普通成员镜像仍由现有 `PaneSnapshotter` + `Mirror` 呈现，无需先设计 SPA DTO；
- 可大量复用 Textual Pilot 测试，做出浏览器可见演示最快。

主要问题：

- “每次访问启动一个 App 子进程”会复制 `console.app` 的进程内状态、`BusPump`、刷新定时器和窗口 fit 行为；多 tab 加本机 TUI 时会放大现有单所有者问题。
- `textual-serve` 传输的是 Textual 自定义协议，不是任意 PTY。现有 F8 会暂停 Textual 并在本地终端执行 `tmux attach`，浏览器端不能因此获得完整接管；要补齐仍需自建 PTY/WebSocket/终端前端，届时已接近路线 B。
- `Ctrl+V` 当前用 Pillow `ImageGrab` 读**服务端 Mac 剪贴板**，不会读取浏览器剪贴板；浏览器上传需要另开 Web API/JS 通道。
- Web 最终仍是终端式网格，难以充分利用浏览器的布局、可访问性、文件能力和未来移动适配。上游自定义协议也增加黑盒集成与升级风险。

实时推送：Textual UI 更新由其 WebSocket 协议自动传输，但 bus/work/team 仍由每个 App 自己轮询；它没有替代公共事件模型，也没有自动解决跨进程一致性和控制租约。

测试：领域与 Textual Pilot 测试可复用；但浏览器协议、子进程生命周期、F8、剪贴板和多 tab 仍需 Playwright + 真实 tmux 的黑盒测试。

wheel/PyPI：无需自建 SPA 构建链，但会增加 `textual-serve` 及其 aiohttp/Jinja 依赖。依赖 wheel 自带静态资源，隔离安装仍要验证入口能在源码仓库外找到全部资源。

结论：适合限时技术验证或内部只读入口，不应作为承诺全功能对齐的正式架构。

## 6. 汇总

| 维度 | A 标准库 + 单页 | B FastAPI + SPA | C textual-serve |
|---|---|---|---|
| 首屏原型速度 | 快 | 中 | 最快 |
| 全功能长期可维护性 | 低 | 高 | 中低 |
| 与现有领域状态共用 | 需先抽控制面 | 需先抽控制面，边界最清楚 | UI 复用最高，但进程状态重复 |
| 任务/消息/名册实时推送 | 长轮询可做 | WebSocket 原生适配 | 由 Textual 协议传 UI |
| 终端 10 Hz 镜像 | 高频长轮询勉强可做 | 独立有界 WS，最合适 | 复用现有 Mirror |
| 浏览器完整接管 | 双通道自研，困难 | PTY + WS，边界清楚 | 仍需另建 PTY 通道 |
| 浏览器剪贴板/上传 | 手写接口 | 浏览器 API + 上传接口 | 现有 ImageGrab 不适用 |
| 多视图并列运行 | 必须新增租约 | 必须新增租约 | 尤其容易重复起 App/pump |
| 测试体系 | pytest + 重浏览器 E2E | pytest/API/组件/E2E 分层最好 | Pilot 可复用，关键能力黑盒 |
| wheel 复杂度 | 最低 | 静态构建与 Web 依赖最高 | 中等，第三方传输栈依赖 |

## 7. 推荐

推荐 **B：同仓库、同 wheel 的 FastAPI/ASGI 后端 + SPA**，并把“抽公共控制面”和“运行时单所有者/交互租约”列为 Web UI 之前的硬前置。

原因不是偏好某个 Web 框架，而是目标已经从群聊页变成完整控制台：任务与消息适合结构化 snapshot/delta，终端镜像需要有界高频流，完整接管需要 PTY 双向字节流，剪贴板需要浏览器 API。这四类流量用明确的 API/WebSocket 边界更可测，也能让 TUI 与 Web 真正共享状态模型而不是互相复制。

建议保留现有 gateway 页面，继续承担低权限远程群聊，不把它升级成完整控制台。路线 C 可以做一次不合入产品路径的限时 spike，用来确认现有 Textual 画面在浏览器中的视觉效果，但不应成为后续 Goal 的依赖。

## 8. 首批 Goal 拆分建议

验收基线直接采用 [功能盘点 §2](inventory.md#2-tui-画面与操作总表) 的条目名。每项都要求不破坏既有 TUI，并可单独合入 `main`；后项只依赖已合入前项。首版只对齐当前 TUI：任务在 Web 中仍以 human 查看、关联沟通为主，不额外开放伪装成 Leader/成员的 `WorkService` 写按钮。

1. **WEB-001 — Web/TUI 共用控制面与读模型**

   下沉任务摘要/详情、timeline 投影、成员状态和控制编排，返回与 UI 无关的 DTO/结果；TUI 改用公共接口但基线不变。直接覆盖 §2 的「左栏任务摘要」「左栏成员卡片」「中栏任务详情」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」的数据语义，以及「成员打断」「成员终止」「成员重启」的控制/审计语义。验证领域层禁止 import `console`，并以现有 Textual 基线证明无行为回归。

2. **WEB-002 — 多前端运行时协调**

   实现每工作区单一 Hub 投递租约、每成员交互/resize 租约和崩溃恢复。直接约束 §2 的「终端窗口适配」「成员画面点击直连」「直连文本输入」「直连编辑/方向键」「全屏接管 / attach」以及「退出」：两个 TUI/测试进程并列时不得重复投递、互相 resize 或交错键入；关闭一个前端不得关闭成员会话。该 Goal 与任何 Web 框架无关。

3. **WEB-003 — Web 后端骨架与一致 snapshot API**

   增加 Web 入口、loopback 默认监听、认证会话和 workspace/team/work/timeline/member snapshot，只调用 WEB-001 的控制面。接口直接覆盖 §2 的「顶部 Header / 工作区副标题」「左栏任务摘要」「左栏工作对话记录卡」「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」。wheel 内带最小静态健康页，源码外安装可启动。

4. **WEB-004 — versioned 实时事件流**

   用 watchfiles/领域动作触发 work、bus、team、roster、member invalidation/delta；实现 `epoch/revision`、重连全量 resync、有界客户端队列和慢客户端测试。实时验收直接针对 §2 的「左栏工作对话记录卡」未读数、「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「健康告警」「成员状态实时更新」。此时可用测试客户端完成，不依赖最终 SPA。

5. **WEB-005 — 桌面 SPA 只读与导航闭环**

   实现 §2 的「顶部 Header / 工作区副标题」「左栏任务摘要」「左栏工作对话记录卡」「左栏成员卡片」「左栏任务列表」「中栏任务详情」「任务证据」「不可覆盖任务事件流」「任务关联工作对话」「工作对话记录时间线」「时间线分类筛选」「时间线滚动」「`/workspace`」「`/task [ID]` / F3」「`/help` / ? / F1」「深浅主题 / T」「健康告警」「成员状态实时更新」「退出」。`/workspace` 与 `/task` 可用等价 Web 导航，不要求复刻斜杠文本命令。加入组件测试和 Playwright 截图自验证。

6. **WEB-006 — 消息、ask/reply 与浏览器附件**

   实现 §2 的「工作对话输入框」「@ 成员补全」「发送 ask/reply」「图片粘贴/发送」「待发图片撤销」。浏览器上传写入现有内容寻址附件存储，响应和下载不泄露绝对路径；消息继续走 `Message.create()` + `deposit()`，默认 Leader、选中 task、上次对象和 ask/task 互斥语义与 TUI 一致。actor 从认证上下文生成，不接受客户端自报。

7. **WEB-007 — tmux 镜像、回滚与受限直连**

   实现 §2 的「成员终端镜像」「成员终端回滚」「终端窗口适配」「成员画面点击直连」「直连文本输入」「直连编辑/方向键」。使用完整帧 WebSocket、最多 10 Hz 合并/背压、ANSI 安全渲染和 canonical size；浏览器坐标必须经最新帧再次确认 composer，所有输入经 WEB-002 租约与 `tmuxctl` 串行。覆盖 TUI/Web 同时观看、抢占、回滚态拒绝输入和断线释放测试。

8. **WEB-008 — 成员管理、生命周期与完整接管**

   实现 §2 的「成员打断」「成员终止」「成员重启」「全屏接管 / attach」「`/up`」「`/down`」「`/restart`」「`/adopt`」「`/mute`」「`/member add/rm/list`」。危险动作保留二次确认与审计；完整接管使用只允许固定 tmux attach 的 PTY bridge，不能开放 shell；`/adopt` 明示进程级临时状态，`/mute` 继续走 Hub 策略。真实 tmux + 浏览器验证租约、断线清理和控制审计。

9. **WEB-009 — 全功能矩阵与发布收口**

   逐行勾验 inventory §2 的全部条目，任何未完成项保留未完成状态；更新产品与架构权威文档、CLI 帮助和安全说明。前端产物进入 sdist/wheel，`qa.release` 在源码外联网安装并启动 Web；验证运行时不依赖 Node、源码路径或 CDN，并复查「退出」只关闭 Web 会话/服务、不关闭成员。

WEB-001～004 是第一开发批的顺序主线；WEB-005 起进入可见 UI。WEB-007/008 是达到“全功能对齐”而不是“只读 Web 看板”的关键完成项。各 Goal 的最终描述应继续复制 inventory §2 的对应条目名作为完整枚举，不能把本节的分组标题当成可删减示例。

## 9. 需 human 拍板的问题

1. **访问边界**：首版是否只监听 `127.0.0.1`？若允许局域网/公网访问，必须同时拍板 TLS/反向代理、登录方式、session 失效和远程危险操作是否继续要求本机二次确认。现有 gateway 的 URL token 不足以直接保护完整控制台。
2. **多端控制策略**：推荐“多人可看、每成员同一时间仅一个交互租约”；租约是显式抢占、请求转交，还是最后活跃端自动获得？TUI 与 Web 冲突时谁优先？
3. **Web 依赖发行方式**：FastAPI/ASGI server 默认随 `amux-team` 安装，还是放在 `amux-team[web]` extra？前者体验一致，后者保持 TUI 安装更轻。
4. **前端栈**：是否接受 TypeScript + Node 只作为构建依赖，并将编译产物打入 wheel？若接受，再在实现 Goal 中从 React/Preact/Vue 或轻量原生组件里选一项；架构不应绑定 CDN。
5. **完整接管定义**：是否接受浏览器中的 PTY→固定 `tmux attach` 作为 F8 对等实现？若只允许现有按键白名单，则 Web 只能称“增强直连”，不能宣称和 TUI 完整接管对齐。
6. **权威文档变更时点**：现有 `docs/product/product.md` 仍把 Web 列为本期不做。建议在创建 WEB Goal 卷时同步改为已拍板范围，避免实现 Goal 与权威产品文档冲突。
