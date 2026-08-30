# 架构决策与技术栈

## §1 分层

```
console (TUI, Textual) ─┐
                        ├── control  UI/HTTP 无关的读模型、控制编排与租约
web (HTTP + SPA) ───────┘      │
                               ├── work       任务账本:责任链、状态机、证据与事件流
                               ├── team       团队档案与工作区绑定
                               ├── workspace  工作区登记与解析
                               ├── bus        消息队列、策略与审计日志
                               ├── tmuxctl    输出流、按键注入、进程控制
                               └── roster     名册、启动适配与生命周期
```

- 模块放在 `src/` 下,`control` 不得 import `console`、Rich/Textual 或 HTTP 框架；其 DTO 只含可 JSON 序列化的 dataclass/枚举/基础类型。颜色、主题和 Rich/Textual 渲染留在 `console`，Web 传输留在 `web`。`team`/`work`/`workspace`/`bus`/`tmuxctl`/`roster` 同样不允许 import `console`(单向依赖)。`team` 可以读取 `workspace` 的状态目录，`work` 只能依赖 `team` 与下层模块。
- `tmuxctl` 是拼装 tmux 命令的唯一出口。启动时探测 tmux ≥ 3.2,不满足则明确报错;会话存在性/结束使用 `=name` 精确匹配,`send-keys` 使用普通会话名(tmux 不接受 `send-keys -t =name`)。
- 通用长文本注入走 `KeyInjector.text`:先按 capture-pane 末行启发式判断是否有未提交输入,有则等待,超时则先 Enter 换行隔离再注入,避免拼接到半行字上。
- 总线向成员投递走 `KeyInjector.deliver`：字面文本与 Enter 分成两次 tmux 注入，默认间隔 10ms，避免 Claude/Codex 把 Enter 吞进同一次粘贴突发。该间隔已在真实 Claude 2.1.251 pane 验证；每条消息在归档前还必须确认 composer 已清空。Claude 双横线输入框按整块内容识别，其他 CLI 才回退光标行指纹。同一收件人严格串行，未确认的队首消息留在 queue 重试且只记一次失败审计，不得继续追加或提前记成 `deliver`；其他收件人不受阻塞。
- 成员画面的点击直连只在当前画面中识别 Claude 底部双横线 composer 或 Codex 底部 `›` prompt；无法确认结构、点击输出区或正在回看 tmux 历史时不得激活。实时字符经 `MemberController` 的单一串行队列走 `tmuxctl`，Enter 作为一次完整 `type` 动作审计；`Ctrl+V` 仍由 console 读取图片，不作为字符透传。输入框删除键按“编辑本地文字 → 撤销最后一张待发图片 → 直连终端透传”排序；撤销只删除消息内的待发引用，不删除可能被其他消息复用的内容寻址文件。
- 工作对话记录由 `control` 投影 `bus/log.jsonl` 与 `work/events.jsonl`：保留原始 `ts` 供显示，同时把总线的本机时区朴素时间与账本 UTC ISO 时间统一成 epoch `at` 供排序；单调时钟值不得进入 DTO。总线收发端是否含 `human`/`im:` 决定 human 往来或 AI 协作，`control` 审计决定终端控制，`WorkEvent` 决定任务事件，禁止解析自由文本猜分类。筛选只改变渲染，不删除底层历史；任务 `reported` 事件是完成时间的权威来源，任务详情的创建/更新/完成时间来自事件重放后的 `Task` 投影。
- `console` 内嵌 hub 的投递循环,但投递循环必须能脱离 TUI 独立运行(headless hub,兼容现在的 `python3 hub.py` 用法)。每工作区一把 `HubDeliveryLease`、每成员一把交互租约(`MemberLeaseManager`,resize/直连/接管共用):TUI `ConsoleApp` 启动时实例化并接到 `BusPump` 与成员交互路径,非持有者只观察;退出只释放租约、不关成员会话。抢占必须显式(TUI 的 F8 接管走 `force=True`)。
- v0 的 `hub.py`、`msg`、`start.sh` 在对应 Goal 完成前保持可用;完成后 `hub.py`/`start.sh` 变成新实现的薄入口,`msg` 接口永不变。`start.sh` 已改为读仓库根 `roster.toml`(见 `src/roster`)。

## §2 技术栈

| 选择 | 定论 | 理由 |
|---|---|---|
| 语言 | Python ≥ 3.11 | 与现有总线同语言;四个协作 AI 都熟练 |
| 运行环境 | `uv` 管理的项目 venv(`uv sync` / `uv run`) | 系统 python 是 3.8(miniconda),不满足要求;不污染系统环境 |
| TUI | Textual | 满足"漂亮 + 低延迟"且纯 Python;富组件、深浅色、CSS 式主题 |
| Web 控制台 | FastAPI/ASGI 后端 + TypeScript SPA(2026-08-30 human 拍板，路线 B)；同仓库同 wheel，Web 依赖默认安装；Node 仅构建期，产物打进 wheel，运行不依赖 Node/CDN；首版只监听 `127.0.0.1`；TUI 与 Web 共用不依赖 UI 的控制面层，每工作区单一 Hub 投递租约、每成员单一交互租约 | 任务/消息适合 snapshot+WebSocket delta，终端镜像需有界高频流，完整接管需 PTY 双向流；详见 [方案比较](../web/architecture-options.md) |
| 剪贴板图片 | Pillow `ImageGrab` + PNG 内容寻址文件 | `Ctrl+V` 由总控台统一读取系统剪贴板；不依赖不同成员 CLI 的粘贴按键实现 |
| 消息存储 | 文件队列(`bus/queue/` 一消息一文件,原子改名)+ `bus/processed/` 归档 + `bus/dead/` 死信 + `bus/log.jsonl` 审计(80 字符预览,全文另存 `bus/bodies/`,10MB 轮转);成员消息只有在终端确认提交后才从 queue 归档并记录 `deliver`;根目录可注入(`BUS_ROOT` 或显式参数,优先级最高)。已登记工作区的默认根是 `~/.amux/workspaces/<slug>/bus/`,互不串台;未登记时回落仓库根 `bus/` | 语言无关、可 tail、崩溃可恢复;规模(单机、几个成员)远够 |
| 文件事件 | `watchfiles`,不可用时回退 0.2s 轮询 | 达成投递延迟预算 |
| 任务存储 | 工作区 `work/events.jsonl` 只追加事件流 + SHA-256 哈希链 | 单机规模无需数据库；可直接审计，并能检测历史删改 |
| tmux | ≥ 3.2,control mode(`tmux -C`)做输出流,普通命令做注入/控制 | 见 tmux Goal 卷 |
| 测试 / Lint | pytest / ruff | 轻量,见 quality 卷 |

## §2.1 发行形态

- PyPI 发行名是 `amux-team`，命令名是 `amux`；源码开发仍允许历史 `console` / `roster` 入口。`amux-dev` 是指向当前 checkout 的开发薄 shim，默认不覆盖 `AMUX_HOME`，因而与正式版共用 `~/.amux` 的团队和工作区状态；显式 `AMUX_DEV_HOME` 才切到隔离状态。
- 源码 checkout 与 wheel 都直接使用 `src/amux_runtime/prompts/` 中的公共、Leader、成员三份 Markdown 提示词；提示正文不写进 Python 或 `AGENTS.md`。`roster.toml` 的源码版本仍是名册权威，构建制品携带受一致性测试保护的名册快照。安装后的 wheel 不探测或依赖源码仓库路径。
- `uv run python -m qa.release` 是制品完成契约：`uv build --no-sources` 后检查 sdist/wheel 内容，并在源码仓库外用全新 uv 缓存联网安装 wheel 与完整依赖。显式 `--offline-smoke` 仅用于断网时检查 payload，不是发布完成证据。
- Python 包不捆绑 tmux 或各厂商 AI CLI；缺少运行器由运行时预检报告。上传使用 GitHub OIDC Trusted Publishing，构建 job 与具有 `id-token: write` 的发布 job 分离。

IM 网关平台:**自建**(human 2026-08-16 拍板)。本机起一个只用标准库的 HTTP 服务,手机浏览器打开即是群聊页;消息进出都经 `bus/queue`,清洗、限频、熔断照常生效。不引第三方 SDK,也就没有账号与凭证问题。

未决项(改动需人拍板):是否引入 sqlite 替代文件队列(除非文件队列实测撑不住,否则不换)。

## §3 冻结契约

1. **消息 JSON**:`{"to","from","text","ts"}` 四字段必备,含义不变;新能力只能加可选字段(如 `kind`、`replyTo`、`id`、`task`、`attachments`),读方必须容忍未知字段。`attachments` 只保存图片的绝对本机路径、媒体类型、名称、宽高和字节数，不把二进制或 base64 塞进文件队列；旧读方会把它当未知字段原样保留。
2. **`./msg` 命令行**:`./msg <收件人> <内容...>` 永远可用(仓库根薄入口);成员面向的形态是 `amux msg`(从当前工作区投递)。发件人取 `AGENT_NAME`,缺省 `human`。新参数只能是可选 flag(如 `--ask`)。
3. **寻址**:工作区内收件人仍是短名(`claude`);tmux 会话名是 `<成员>@<slug>`,双向映射收口在 `workspace.session`(`bind_tmux` 是生产路径唯一入口)。`human` 是保留名,不投递、只上屏;`bus` 是总线自身的署名(防环拒收回执);`im:` 前缀是 IM 网关代投的远程身份(同样只上屏,由网关发回群)。这三者没有 tmux 会话,不参与 slug 拼接,成员不得占用这些名字。
4. **运行时提示词**:`src/amux_runtime/prompts/` 是唯一事实来源；`common.md` 对所有成员生效，`leader.md` / `member.md` 根据工作区名册中的 `role` 追加。`team activate` 必须把团队 ID、角色、Leader、模型和职责完整投影并持久化，未绑定团队的成员只加载公共提示。为让共用 `~/.amux` 的 PyPI 0.1.0 仍能读取开发版生成的 `members.toml`，角色元数据编码在旧版已经允许的 `[custom.env]` 中(`AGENT_ROLE`、`AMUX_TEAM_*`、`AMUX_AGENT_*`)，不新增旧版会拒绝的顶层键；新代码加载时还原为类型化 `Member` 字段。正文不得复制进 Python、`AGENTS.md`、团队档案或名册(由 ROS-006/007、TEAM-006 钉住)。
5. **团队档案**:`~/.amux/teams/<id>.toml` 是 Leader、成员、模型偏好、职责与经验证启动适配（`command`/`args`/`env`）的唯一来源；`workspaces/<slug>/team.toml` 只保存所选团队 ID。`amux team add-member` 读取档案后构造候选数据，以 `team_from_dict` 全量复验、用 `shutil.which` 预检 command，再通过同目录临时文件 + `os.replace` 原子写回；不绑定工作区、不改 `members.toml`、不启动进程。`--preset claude|codex` 只借默认 `fable-core` 同类 runner 的 command、env 与固定权限 flags；Claude 的 `--model`/`--effort` 从本次参数生成，Codex 的 `-m`/`model_reasoning_effort` 同样从本次参数生成，`service_tier` 由 speed 生成，显式 `--command`/`--arg`/`--env` 可覆盖适配。`amux team activate` 将已校验的团队适配完整投影为该工作区 `members.toml`，再由 `roster` 负责生命周期；无适配的团队不能激活。默认 Fable 团队的三个 Claude 成员通过 `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` 保留 tmux 回滚区，否则 Claude fullscreen 的 alternate screen 会令 `history_size=0`，`capture-pane -S` 没有历史可读；同一适配把 `NO_COLOR` 设为空，覆盖 amux 调用环境可能带入的 `NO_COLOR=1`，让 Claude 继续向 tmux 网格输出 ANSI 样式。颜色捕获仍统一走 TMX-004 的 `capture-pane -e`，console 不自行猜测或重绘成员配色。
6. **任务事件(TEAM-002)**:任务的建立/拆分、派工、进展/阻塞/证据、提交、评审/退回、重新分派、接管、最终验收和 human 汇报必须追加到工作区 `work/events.jsonl`。事件以连续 `seq` 和 `prev/hash` 构成 SHA-256 哈希链；任务当前状态只由重放得到，不另存可覆盖快照。只有任务记录的唯一 Leader 可以建立、派工、接管、验收和最终汇报；执行者不能最终结项，评审者不能与执行者相同。接管事件必须包含原因、范围、原成员已交付内容和后续验收方式。总线可选 `task` 字段只用于把沟通关联进详情，不改变四个必备字段，也不能代替责任事件。Leader 的运行时提示按失败后果、改动范围、可逆性和不确定性选择验收深度：低风险任务采用最小充分验证且不默认安排独立评审，高风险信号才升级测试/评审并说明原因；这只改变验收方法，不改变唯一 Leader 的状态权限和最终责任。

## §4 安全边界

- 一切经总线到达的文本都是**不可信输入**:注入终端前剥 ANSI/控制字符(C0、CSI、OSC);上屏前同样清洗。实现在 `src/bus/sanitize.py`,两个入口分别是 `format_for_injection`(投递)和 `format_for_screen`(上屏),新增出口一律走这两个函数。
- 危险操作(push、删文件、装软件、出仓库)只认**本机** human 的直接指令,写在公共运行时提示词里;网关侧再挡一层:来自 IM 的这类指令一律挂起,由本机 `gateway approve` 放行后才入队(远程指令弱于本机指令,手机上自称 human 也不例外)。
- 总控台自身不做"自动替人点权限弹窗"这类模拟操作;权限放行走各 CLI 的正规配置(roster 卷)。
- 默认启动适配固定为：Claude 使用 `--permission-mode auto`，由 Claude 的自动分类器处理工具调用；GPT/Codex 使用 `-s danger-full-access -a never`，关闭 Codex 命令沙箱和人工审批，获得完整文件与命令权限。两者仍受账号/组织策略及 macOS、SIP、文件 ACL 等操作系统边界约束。该策略由 human 于 2026-08-30 拍板，取代 ROS-002/008 的 `acceptEdits` 与 `workspace-write + on-request`。

## §5 工作区

一个工作区 = 一个被 amux 服务的项目目录。状态不进用户仓库:

```
~/.amux/                          # AMUX_HOME,测试注入临时目录
  config.toml                     # 全局默认成员/自动拉起/默认主题(WS-012)
  teams/<id>.toml                 # 可复用团队:唯一 Leader、成员与模型偏好(TEAM-001)
  paths.toml                      # 绝对路径 → slug 反查
  workspaces/<slug>/
    workspace.toml                # 项目根路径(源数据)
    team.toml                     # 当前工作区绑定的团队 ID(TEAM-001)
    members.toml                  # `team activate` 投影的成员角色、职责和启动适配(TEAM-003/006)
    attachments/                  # Ctrl+V 图片；PNG 内容寻址、文件权限 0600
    bus/                          # 该工作区的队列、审计、ask/reply(WS-003)
    work/events.jsonl             # 只追加任务事件、证据与责任链(TEAM-002)
```

- **slug**:默认取项目目录名;两个项目同名时自动 `name-2`、`name-3`。显式 `--slug` 撞名则报错不覆盖。`:` 和 `.` 禁止出现在 slug 里(tmux 会静默吃掉,见工作区 Goal 卷「已知陷阱」)。
- **解析**:从任意 cwd 向上走,命中已登记的项目根即为所属工作区;嵌套时取最近的那一个。未登记时,`amux` / `amux msg` / `amux member` 自动把**当前目录**登记为工作区,不再回落 amux 自己的仓库根。显式 `amux workspace add` 仍要求目录名能当 slug,否则 `--slug`;裸跑自动登记时非法目录名会被收成合法 slug(挤不出来就用 `ws`)。
- **全局 `config.toml`**:可选,不随裸跑 `amux` 自动创建;显式 `amux config init` 才在 `~/.amux/config.toml` 写入默认四成员、自动拉起和深色主题。`[workspace].default_members` 是无本地名单时的默认成员,`[lifecycle].auto_start_members` 决定进入 amux 是否幂等地拉起这些成员,`[console].theme` 是默认主题。`amux config show` 显示生效值。
- **项目侧 `amux.toml`**:可选。amux 不在用户项目里创建这个文件。成员名单合并规则:
  1. 仓库根 `roster.toml` 是预设目录(四个 CLI 的启动命令与参数),**不自动启用**。
  2. 工作区成员名单在 `~/.amux/workspaces/<slug>/members.toml`;有这份文件就以它为准。`amux member add|rm|list` 与界面 `/member add|rm|list` 改它。
  3. 没有 members.toml 时,项目根 `amux.toml` 的 `enabled` 优先;名单出现未知名字则报错。
  4. 两者都没有时,采用全局 `config.toml` 的 `default_members`;全局文件也没有时才是空名册。
  5. `[env]` 覆盖到每个启用成员的 env,项目侧同名键赢。
- **成员 cwd**:`Lifecycle` / `HealthSupervisor` 默认落到当前工作区项目根。
- **CLI**:`amux workspace add|list|rm|current|gc|migrate`;`amux member add|rm|list`;`amux config init|show`;`amux` 按 cwd 自动选工作区(未登记则登记当前目录),`--workspace <slug>` 显式指定;`amux msg` 从 cwd 定位工作区总线。控制台标题栏显示当前 slug 与项目根,`/workspace <名字>` 切换绑定(成员栏与工作对话记录跟着换)。IM 网关按房间名(或 `workspace` 字段)把消息投进对应工作区总线,白名单可按 `[workspaces.<slug>]` 分开放。`rm` 先关掉该区 `<成员>@<slug>` 会话再删状态目录,不碰用户项目文件;`gc` 回收已经没了登记的孤儿会话。并发上限写在 `~/.amux/limits.toml`,超限只告警不拒绝。从旧布局升级:`amux workspace migrate` 把仓库根 `bus/` 拷进 `~/.amux/workspaces/<slug>/bus/`,源目录先留着,核对后可删;回退是 `amux workspace migrate --rollback`,把工作区总线拷回仓库根 `bus/`。两条都是拷贝,谁都不自动删对方。`uv run console` / `roster` / `hub.py` / `start.sh` 在单工作区下用法不变;显式 `--bus-root` 永远最高优先,未登记时仍回落仓库根 `bus/`。
- 同一成员允许同时在多个工作区各跑一份(产品定义已拍板)。并发上限不设硬封顶,只告警(WS-009)。
