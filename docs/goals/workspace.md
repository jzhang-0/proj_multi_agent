# 工作区(WS)

让 `amux` 从「只服务自己这个仓库」变成「装在机器上、可以同时服务多个项目」。产品侧定义见 [产品定义 · 多工作区](../product/product.md);本卷是工程拆分。

**一句话目标**:在项目 A 和项目 B 各敲一次 `amux`,两组成员同时活着、各自跑在自己的项目根、消息互不串台。

## 已定的设计(不要在实现时推翻,要推翻先问 human)

1. **状态集中放 `~/.amux/workspaces/<slug>/`,不往用户项目里塞目录。** 用户的项目是别人的仓库,amux 不该在里面留 `.amux/` 让人去 gitignore。项目侧只允许一个**可选**的 `amux.toml`(声明启用哪些成员、额外 env),没有它也要能跑。
2. **会话名格式 `<成员>@<slug>`。** 实测结论见下面「已知陷阱」——`@` 安全,`:` 和 `.` 会被 tmux 静默吃掉。
3. **工作区内的寻址方式一个字不改。** 成员之间仍然写 `msg claude`,短名 → `claude@<slug>` 的映射收口在边界一层。理由:群聊协议是成员依赖的公共契约(产品定义 · 设计原则 2),四个 CLI 的使用习惯不能因为内部重构而变。
4. **一个 console 实例服务一个工作区。** 标题栏显示是哪个,`/workspace` 切换绑定。「一个界面同时并排看多个工作区」不在本卷范围,要做另开 Goal。

## 需要 human 拍板的(动工前问,别自己定)

2026-08-17 human 拍板:

- **slug 生成规则**:取目录名;撞了自动加 `-2`、`-3` 后缀。含 `:` / `.` 等非法字符的目录名不能直接做 slug,要求 `--slug` 另起名。
- **同一个成员能否同时在多个工作区跑**:允许(与产品定义「claude 可以同时在多个项目里各跑一份」一致)。
- **上限值**:不设硬上限,只告警(WS-009 落地可配告警,超限不拒绝拉起)。
- **老数据**:迁进 `~/.amux/workspaces/<slug>/bus/`;仓库根 `bus/` 留到迁移命令验证完再提示可删(WS-010)。

## 已知陷阱(实测过的,别再踩)

- **tmux 会话名里 `:` 和 `.` 有坑**:2026-08-17 实测,`new-session -s claude:demo` 会被解析成会话 `claude`(`:` 是 session:window 分隔符)从而**静默命中已存在的同名成员会话**;`claude.demo` 被静默改写成 `claude_demo`。`@` 和 `/` 才会原样保留。这个坑当场就造成过一次误杀真实成员会话。
- **`cwd` 这条接缝已经铺好但没人传值**:`start_member(cwd=)` → `Lifecycle(cwd=)` → `HealthSupervisor(cwd=)` 参数全在,全仓库没有一处传实参,一路回落到 `repo_root()`。WS-004 是接上它,不是新建。
- **`bus/` 的路径注入也已经做好**:`BusPaths.resolve()` 支持显式参数和 `BUS_ROOT` 环境变量(BUS-001 的产物)。WS-003 是在它上面加一层工作区解析,不要另起炉灶。
- **`./msg` 是 amux 仓库根的脚本文件**:成员 cwd 一旦离开 amux 仓库,群规里「在仓库根运行 `./msg`」就断了,四个成员会集体失声。WS-005 必须先于 WS-004 落地或与之同时合入,否则 main 上会出现「成员起来了但发不出消息」的中间态。
- **会话名的引用面有 32 处**:tmuxctl 15、roster 11、qa 4、console 2;`bus/` 和 `gateway/` 是 0(它们按收件人名寻址,名字→会话的映射在 hub 一层)。WS-002 要把这 32 处收口到一个映射模块,别逐处改。
- **`human` 和 `bus` 是保留名,`im:*` 是网关身份**,这三者没有 tmux 会话,加命名空间时不要给它们拼 slug(见 `src/bus/hub.py:34`、`src/roster/schema.py:9`)。

## Goal

- [x] **WS-001** — 工作区模型:定义 workspace(项目根路径 + slug + 状态目录);`~/.amux/workspaces/<slug>/` 布局与 `path` 反查记录;从任意 cwd 向上找到所属工作区的解析 API(找不到时的行为要明确);项目侧可选 `amux.toml` 的 schema 与加载(缺失即用默认);`amux workspace add|list|rm|current` 四个子命令。slug 规则按 human 拍板结果实现。
  - 前置:无(本卷根 Goal)。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace.py tests/test_console_app.py tests/test_engineering_skeleton.py -q`
  - 证据:`src/workspace/`(`Workspace` 三元组、`Store` 把源数据放 `~/.amux/workspaces/<slug>/workspace.toml`、反查写 `paths.toml`、`AMUX_HOME` 可注入);slug 取目录名、撞了 `name-2`/`name-3`,含 `:`/`.` 明确报错;`resolve_from_cwd` 向上走取最近登记根,找不到提示 `amux workspace add` 且不回落 amux 仓库;`load_project_config` 缺文件即默认;`amux workspace add|list|rm|current` 经 `console.cli` 分发,`rm` 不碰项目文件。`tests/test_workspace.py` 覆盖以上枚举;2026-08-17 `29 passed`,ruff 干净。旧入口 `--headless`/`--version` 回归仍过。
- [x] **WS-002** — 会话命名空间:成员名 ↔ tmux 会话名的双向映射收口到单一模块,格式 `<成员>@<slug>`;`human`/`bus`/`im:*` 不参与拼接;现有 32 处直接把成员名当会话名用的调用点全部改走映射;非法字符(`:`、`.`)在 slug 生成与成员名校验两处都拦掉并给明确报错。
  - 前置:WS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_session.py tests/test_roster_load.py tests/test_roster_lifecycle.py tests/test_console_health.py tests/test_console_control.py -q`
  - 证据:`src/workspace/session.py`(`SessionNames` 双向映射、`NamespacedTmux` 把成员短名翻译成 `<成员>@<slug>`、pane id 不翻译、已带后缀不二次拼接;`human`/`bus`/`im:*` 抛 `SessionNameError`);生产路径 `bind_tmux()` 收口 hub/roster/console 三处 `Tmux()` 构造。slug 侧 WS-001 已拦 `:`/`.`,成员名 `validate_member_name` 同样拦并说明 tmux 静默行为。守卫测试禁止这三处再直接 `Tmux()`。2026-08-17 相关测试 90 passed,ruff 干净。未登记工作区时映射恒等,单工作区旧会话名先不改。
- [x] **WS-003** — 总线按工作区隔离:`BusPaths` 增加工作区维度(队列、processed、死信、asks、replies、`log.jsonl` 全部隔离);`--bus-root` / `BUS_ROOT` 保留为显式覆盖且优先级最高;跨工作区不串消息的回归测试;审计日志里记录工作区归属。
  - 前置:WS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_bus.py tests/test_bus_core.py tests/test_bus_audit.py tests/test_v0_contract.py -q`
  - 证据:`BusPaths.resolve` 优先级显式 > `BUS_ROOT` > `~/.amux/workspaces/<slug>/bus` > 仓库根 `bus/`;`for_workspace` 把队列/死信/ask/审计全部放进该工作区状态目录;`AuditLog` 在有 slug 时写入 `workspace` 字段。`tests/test_workspace_bus.py` 覆盖两工作区不串队列、覆盖优先级、审计归属。2026-08-17 相关 60 passed,ruff 干净。
- [x] **WS-004** — 成员落到工作区目录:接上已存在的 `cwd` 接缝,`Lifecycle`/`HealthSupervisor` 构造时传入工作区项目根,成员进程 cwd = 该项目根(用 `lsof -a -p <pid> -d cwd` 实测取证,不看代码推断);名册分层——全局默认名册(四个成员的启动参数)+ 项目 `amux.toml` 覆盖(启用哪些、额外 env),合并规则写进文档。
  - 前置:WS-001、WS-002。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_members.py tests/test_roster_lifecycle.py tests/test_roster_health.py tests/test_roster_load.py -q`
  - 证据:`Lifecycle`/`HealthSupervisor` 默认 `cwd=project_root_for_members()`;`load_effective_roster` 按架构 §5 合并全局 `roster.toml` 与项目 `amux.toml`。`tests/test_workspace_members.py` 用 `lsof -a -p <pid> -d cwd` 确认 pane 进程 cwd 是项目根,不是 amux 仓库。与 WS-005 同批合入。
- [x] **WS-005** — `msg` 全局化:`amux msg <名字> <内容>` 从当前目录向上定位工作区并投进对应总线;`--ask` / `--reply` / 四字段 JSON / `human` 保留名语义一字不变(BUS-009 的契约测试必须继续通过);amux 仓库根的 `./msg` 保留为薄入口。**与 WS-004 同批合入**,否则成员起在别的目录却发不出消息。
  - 前置:WS-001、WS-003。
  - 验证:`uv run pytest tests/test_workspace_members.py tests/test_v0_contract.py tests/test_bus_ask_reply.py -q`
  - 证据:`console.cli` 把 `amux msg` 转给 `bus.cli`;`BusPaths.resolve` 从 cwd 进该工作区总线。`./msg` 仍是仓库根薄入口。BUS-009 契约测试继续通过。2026-08-17 相关 75 passed,ruff 干净。
- [x] **WS-006** — 群规与开场白同步:`AGENTS.md`「群聊协议」里的 `./msg` 改为全局命令形态;开场白里告诉成员它在哪个工作区、项目根在哪;ROS-006 的单一事实来源一致性检查跟着更新,防两处漂移。
  - 前置:WS-005。
  - 验证:`uv run ruff check . && uv run pytest tests/test_roster_protocol.py tests/test_roster_profiles.py tests/test_bus_ask_reply.py tests/test_tmuxctl_inject.py tests/test_bus_sanitize.py -q`
  - 证据:`AGENTS.md` 群聊协议发消息改为 `amux msg`(任意目录投进当前工作区总线);`render_member_greeting` 注入「你现在在工作区 {slug},项目根是 {root}。」;`check_single_source` 要求协议含 `amux msg` 并拒绝「在仓库根运行 ./msg」;`sanitize` 注入指引与 `.claude/settings.json` 同步。`tests/test_roster_protocol.py` 覆盖开场白工作区行与漂移检查。2026-08-17 相关 68 passed,ruff 干净。`./msg` 仍是仓库根薄入口。
- [x] **WS-007** — 控制台工作区维度:`amux` 按 cwd 自动选工作区,`--workspace <名字>` 显式指定;标题栏显示当前工作区与项目根;成员栏只列本工作区成员,时间线只收本工作区流量;`/workspace <名字>` 切换绑定;`/help` 与 `?` 帮助面板同步。切换后按 QA-004 流程截图自验证。
  - 前置:WS-002、WS-003、WS-004。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_console.py tests/test_console_commands.py tests/test_console_keyboard.py tests/test_console_app.py tests/test_engineering_skeleton.py -q`;看画面:`uv run python -m qa.visual --goal WS-007 --scene workspace-alpha --size 120x30 --fixture workspaces` 与 `--scene workspace-beta --keys Tab,Tab --type "/workspace beta"`、`--scene workspace-help --keys '?'`、`--scene workspace-alpha-min --size 80x24`
  - 证据:`bind_runtime` 按 `--bus-root` > `--workspace` > cwd 选区;`ConsoleApp` 标题栏 `slug · 项目根`,成员栏走该区 `amux.toml`,`/workspace` 换绑总线/成员/时间线。截取物 `tests/baseline/ws-007-workspace-alpha-120x30.txt`:标题 `总控台 — alpha · …`,左栏只有 claude/codex,时间线只有「alpha 专属流量」。`tests/baseline/ws-007-workspace-beta-120x30.txt`:切换后标题变 `beta`,左栏只剩 cursor,时间线换成「beta 专属流量」、alpha 那条消失。`tests/baseline/ws-007-workspace-help-120x30.txt`:`?` 面板「斜杠命令补全,含 /workspace」。`tests/baseline/ws-007-workspace-alpha-min-80x24.txt`:80×24 下列表、时间线、输入框仍可用。2026-08-17 相关 37 passed,ruff 干净。
- [x] **WS-008** — 网关按工作区路由:IM 进来的消息带工作区归属并路由到对应总线;白名单按工作区配置;GATE-004 的来源标记、清洗、限频、危险指令本机二次确认在多工作区下全部继续生效(远程指令弱于本机指令这条不能被绕过)。
  - 前置:WS-003、GATE-004。
  - 验证:`uv run ruff check . && uv run pytest tests/test_gateway_workspace.py tests/test_gateway_security.py tests/test_gateway_base.py tests/test_gateway_local.py -q`
  - 证据:`gateway.workspaces.WorkspaceBinder` 按房间名/slug 投进 `BusPaths.for_workspace`;消息 `extra.workspace` 留下归属;`gateway.toml` 的 `[workspaces.<slug>]` 分开放 users/rooms。GATE-004 在多区下仍在:发件人 `im:`,`sanitize`、限频、危险指令挂在**该区** pending,自称 human 也不能绕过。`tests/test_gateway_workspace.py` 覆盖两区不串队列、按区白名单、确认放行仍带 `im:`。2026-08-17 相关 50 passed,ruff 干净。
- [x] **WS-009** — 资源守护与清理:并发工作区数与成员总数上限可配、超限时拒绝拉起并给明确提示;`amux workspace rm` 关掉该工作区全部会话并清理状态目录(不碰用户的项目文件);孤儿会话(状态目录已删但 tmux 会话还在)的发现与回收。
  - 前置:WS-002。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_guard.py tests/test_workspace.py tests/test_roster_lifecycle.py tests/test_workspace_session.py -q`
  - 证据:按 2026-08-17 拍板,`~/.amux/limits.toml` 的 `warn_workspaces`/`warn_members` 超限**只告警不拒绝**(Goal 原文写拒绝,以拍板为准)。`amux workspace rm` 先杀 `<成员>@<slug>` 再删状态目录,项目文件不动;`amux workspace gc` 回收已无登记的孤儿会话。`tests/test_workspace_guard.py` 覆盖告警仍可 add/up、rm 只关本区会话、gc 不误杀仍在登记的区。2026-08-17 相关 41 passed,ruff 干净。
- [x] **WS-010** — 迁移与旧入口兼容:仓库根现有 `bus/` 数据按 human 拍板结果处理;`uv run console` / `roster` / `hub.py` / `start.sh` 四个旧入口在单工作区下行为一字不变(契约测试钉死);从旧布局升级的一次性迁移命令与回退方式各写一条。
  - 前置:WS-003。
  - 验证:`uv run ruff check . && uv run pytest tests/test_workspace_migrate.py tests/test_workspace.py tests/test_v0_contract.py tests/test_engineering_skeleton.py tests/test_roster_load.py -q`
  - 证据:按 2026-08-17 拍板,`amux workspace migrate` 把仓库根 `bus/` **拷进** `~/.amux/workspaces/<slug>/bus/`,源目录先留着并提示核对后可删;目标已有数据拒绝覆盖,除非 `--force`。回退是 `amux workspace migrate --rollback`,把工作区总线拷回仓库根 `bus/`,两边都不自动删。`BusPaths.resolve` 迁完从项目 cwd 读到新位置;显式 `--bus-root` 仍最高优先;未登记仍回落仓库根 `bus/`。四个旧入口契约钉在同文件:`uv run console --headless --once --bus-root`、`hub.py --once --bus-root`、`start.sh` 仍是 `python -m roster` 薄入口、`roster` 的 `start.sh [stop|<名字>]` 用法不变。架构 §5 与 README 各写了迁移/回退一条。`tests/test_workspace_migrate.py` 覆盖以上枚举。2026-08-17 相关 52 passed,ruff 干净。

质量收口在 [质量卷](quality.md) 的 QA-006。
