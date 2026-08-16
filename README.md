# proj_multi_agent — 总控台

一台机器上多个 AI CLI(claude / codex / cursor / agy)组成一个"群":互相 @ 协作,人从一个总控台里看到一切、指挥一切。产品形态与硬指标见 [产品定义](docs/product/product.md)。

## 现状:v0 总线(可用)

```bash
./start.sh          # 拉起全部成员,本窗口变成群聊记录(hub)
./msg claude 写一个fizzbuzz 写完让cursor review 通过后向我汇报
./msg --ask claude 这个改动是否已经通过测试
./msg --reply <ask-id> 已通过,验证命令见日志
tmux attach -t codex   # 围观某个成员(Ctrl-b d 退出)
./start.sh stop     # 收工
```

- 收件人名字 = tmux 会话名;`human` 保留给人,消息只显示在 hub 窗口。
- 消息单行注入;成员正忙时排进其输入队列。全部流量留档 `bus/log.jsonl`。
- `--ask` 默认阻塞等待关联回复 10 分钟;收件人按投递消息里的 `--reply <ask-id>` 指引答复。可用 `--timeout <秒>` 缩短等待。
- 各成员的免权限弹窗配置见 `start.sh` 与 [roster Goal 卷](docs/goals/roster.md)。
- `./msg` 与 `hub.py` 现在是 `src/bus/` 的薄入口(用法一字未变,自己会切到项目 venv),第一次用前在仓库根跑一次 `uv sync`。

## 开发中:总控台

这是一个多 AI 协作开发的仓库。开发者(即群成员)从 [AGENTS.md](AGENTS.md) 入口开工,任务全部在 [docs/goals/](docs/goals/README.md)。

工程环境由 `uv` 管理,要求 Python ≥ 3.11:

```bash
uv sync
./install-amux.sh      # 把 amux 装成全局命令(只装这一个;uninstall 卸载)
amux                   # 任意目录裸跑,进总控台
uv run pytest -q
uv run ruff check .
```

`amux` 是总控台的正名。装完之后在任何目录敲 `amux` 都进同一个界面——bus 运行时目录和 `roster.toml` 目前仍按源码位置向上找仓库根定位。仓库内开发时 `uv run amux` 等价,`uv run console` 是保留的旧别名(历史 Goal 证据里的命令继续可用)。

工作区登记(状态在 `~/.amux`,不往用户项目里写目录):

```bash
amux workspace add [路径]     # 把项目登记为工作区;同名目录自动 slug-2
amux workspace list
amux workspace current        # 从当前目录向上找所属工作区;找不到就报错
amux workspace rm <slug>      # 取消登记,不碰项目文件
```

项目根可以放可选的 `amux.toml`(启用哪些成员、额外 env);没有也能跑。测试用 `AMUX_HOME` 把状态指到临时目录。

`amux` 起全屏 TUI(内嵌总线投递循环,`q` / Ctrl-C 干净退出,不影响任何成员会话);`amux --headless` 等价于纯 hub 模式(和 `python3 hub.py` 同一份实现)。界面是「会话列表 + 一块主画面」:左边窄列表第一项是群聊(带未读数),后面每个成员一项;选中谁,右边主画面就显示谁——群聊显示时间线,成员显示它的终端画面镜像(Esc/F2 回群聊)。打开成员会话时会把它的 tmux 窗口调成主画面大小,让画面铺满(`--no-fit` 关掉;F8 接管前自动把尺寸还给 tmux)。成员画面用 PgUp/PgDn 或滚轮直接往上翻它自己的回滚区,不必先 Tab 过去。输入框在底部通栏,**在成员会话里不带 `@` 的一行直接键入该成员的终端**(等于在它自己窗口里敲,动作进审计日志),`@名字` 开头仍然走群聊总线。系统 Python 版本不满足要求时也不要绕过 `uv run`。

硬指标实测:`uv run python -m qa.perf`(产品定义四条延迟预算一次跑完:入队→注入终端、消息→时间线上屏、详情画面刷新、键入回显,打印分布并按预算判定,退出码 0/1)。

投递延迟实测:`uv run python -m bus.bench`(起临时 tmux 会话跑 `cat` 当收件人,打印 min/P50/P95/max 并按 P95 < 200ms 判定;`--fake` 只量总线自身调度)。

批 1 收口冒烟:`uv run python -m qa.smoke`(假成员 `cat` 窗格 → 入队 → 投递 → 窗格收到,打印入队到上屏延迟;用临时目录,不碰仓库根 `bus/`)。

四个真实成员的协作实测证据可用 `uv run python -m qa.collab verify` 离线复验；它检查派活、三路回报、最终汇报的入队/投递审计，以及真实 F5 控制事件和 160×40 总控台截取物。复现真实流程见 [协作实测文档](docs/quality/collaboration-check.md)。

投递一条消息 = 一次 tmux 调用:文本和 Enter 用 `send-keys ... ; send-keys Enter` 塞进同一条命令(`Tmux.send_line`),中间不留让成员 CLI 重绘的缝隙,注入前也不再抓画面。投递之后由后台线程确认「那行字真的提交出去了」(`KeyInjector.ensure_submitted`):光标还停在自己刚注入的那行字上就补 Enter,补不动就在 `bus/log.jsonl` 记一条 `deliver-failed`。确认放后台是因为它要等成员 CLI 处理 0.1 秒,挂在投递循环上会把下一条消息的延迟一起抬高(实测 P95 194ms → 345ms)。

消息总线模块是 `src/bus/`:消息 schema v1(`to/from/text/ts` 必备,`id/kind/replyTo` 可选,未知字段原样保留)、文件队列、死信目录、投递循环。bus 运行时根目录默认是仓库根 `bus/`,可用环境变量 `BUS_ROOT` 或 `BusPaths.resolve(root)` 重定向(测试一律指向临时目录)。

tmux 控制层是 `src/tmuxctl/`:启动时探测 tmux ≥ 3.2,并把 `has-session` / `new-session` / `kill-session` / `send-keys` / `capture-pane` / `list-panes` 收口为类型化 API;`PaneOutputStream` 用 control mode 订阅输出并在不可用时回退 pipe-pane FIFO;`ActivityTracker` 只按输出字节活动推断 working/idle/stuck/dead;`PaneSnapshotter` 提供带色/去色与历史快照,并把同窗格高频捕获合并到最多 10Hz;`ProcessController` 提供进程树与打断/终止/强杀分级控制;`CrashMonitor` 用 pane-died hook + 轮询检测崩溃并原地 respawn。其他模块不要直接拼 tmux 命令。

成员可在 `roster.toml` 中设置 `auto_respawn = true` 开启无人值守恢复（缺省关闭）。`HealthSupervisor` 会为所有崩溃发布状态更新；开启恢复时，死窗格原地 respawn，整个会话消失则重新创建，连续三次失败后进入 `failed` 并停止重试，需显式 `reset_failed()` 解除熔断。

`SessionAdopter.discover()` 可发现静态名册外的现有 tmux 会话，`adopt(name)` 一步收编为可寻址的临时成员；`member_names()` 是收件人补全、成员栏和时间线着色的统一名称集合。收编记录只在当前进程内存中，重启不会写入或改动 `roster.toml`，`forget(name)` 也不会关闭用户原有会话。

总控台成员栏由 `MemberStatusService` 驱动：它把每个 pane 的 `PaneOutputStream` 交给 TMX-007 `ActivityTracker`，每 0.5 秒刷新 `idle/working/stuck/dead/failed` 图形徽标、未投递队列数和最后输出相对时间；成功投递会标记成员正在工作，ROS-004 的熔断状态可通过 `mark_failed()` 覆盖为 `failed`。

选中成员后可用 F5 打断、F6 终止、F7 重启、F8 全屏接管；终止和重启必须在默认焦点为“取消”的弹窗中二次确认。接管会暂时挂起 Textual，再进入 `tmux attach-session`，退出 attach 后恢复总控台；所有实际控制尝试都以 `control` 事件写入 `bus/log.jsonl`。

所有工作区域都能用 Tab / Shift+Tab 循环到达（主画面上只有当前那个内容参与循环），聚焦后可用方向键和 PgUp/PgDn 操作会话列表、时间线与成员回滚区。在非输入区按 `?`、或在任意位置按 F1 可打开快捷键帮助；帮助面板本身可用 PgUp/PgDn/Home/End 滚动，Esc 关闭后恢复原焦点。

总控台每 0.5 秒探测 tmux server、各成员会话和 `bus/queue/` 可写性；故障与恢复只在状态变化时写入时间线。tmux server 不在时不重复报每个成员缺失，bus 恢复可写后会自动重启已退出的投递线程；人类输入的入队失败也会显式告警并保留内容供恢复后重试。

成员名册是 `roster.toml`(由 `src/roster` 加载校验)。成员启动开场白由 `AGENTS.md` 的「群聊协议」章节动态生成,roster 不保存协议副本；`check_single_source()` 与回归测试负责防漂移。`./start.sh` 是读名册的薄入口。生命周期直接用 `roster` 命令,单个成员或全体都行,而且幂等(已经在跑的不会被顶掉):

```bash
uv run roster up            # 拉起全部启用成员(已在跑的跳过)
uv run roster up claude     # 只拉一个
uv run roster restart codex # 关掉再拉起
uv run roster down          # 全部关掉(含已停用成员的残留会话)
```

各成员免弹窗参数与残留弹窗写在 `roster.toml` 注释里;claude 的 `./msg` 白名单在 `.claude/settings.json`。

## 手机接入(自建 IM 网关)

```bash
uv run python -m gateway        # 打印带口令的地址,手机同 WiFi 打开就是群聊页
```

第一次跑会在 `gateway.toml` 里生成访问口令(该文件已 gitignore,别提交),并要在同一个文件里写白名单 `users = ["你的名字"]`——**白名单是空的时候网关谁都不服务**。远程指令弱于本机指令:手机上发的消息里出现 push / 删文件 / 装软件 / 出仓库这类要求时不会直接转给成员,而是挂起等本机确认:

```bash
uv run python -m gateway pending          # 看有哪些待确认
uv run python -m gateway approve <编号>   # 本机点头,网关下一轮转给成员
uv run python -m gateway reject <编号>    # 不同意,直接丢弃
```

页面只用标准库提供,不依赖任何第三方账号;消息进出都经 `bus/queue`,清洗、限频、熔断照常生效。手机上的人在总线里的身份是 `im:<名字>`,成员回给他的消息由网关代投回群。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | AI 入口:群聊协议 + 工作规则 |
| `docs/` | 产品定义、架构决策、Goal 清单 |
| `pyproject.toml` / `uv.lock` | Python、依赖、pytest、ruff 与命令入口配置 |
| `src/` / `tests/` | 应用源码与自动化测试(`src/bus`、`src/console`、`src/tmuxctl`、`src/qa`、`src/roster`) |
| `hub.py` `msg` `start.sh` | v0 总线入口(`start.sh` 现读 `roster.toml`) |
| `install-amux.sh` | 把 `amux` 装成全局命令的薄 shim(`uninstall` 卸载) |
| `roster.toml` | 成员名册(名字、启动命令、启用与否、自动恢复配置) |
| `bus/` | 运行时数据(gitignore) |
| `reference/pi-extensions/` | 参考实现:pi 的 talk 扩展(只读,防环策略的出处) |
