# 团队与协作账本(TEAM)

`amux` 的目标从“多 AI 群聊”升级为“可追责的 AI 协作团队”。群聊保留为协作事件的一个沟通渠道；任务、派工、验收和接管必须有结构化记录，供人类回看。

## 已定的设计(2026-08-22 human 拍板)

1. 每个团队恰有一名 Leader。Leader 直接面向 human，负责拆解、分派、推进、验收和对结果负责。
2. 成员只能提交进展、证据和评审意见，不能把任务标为最终完成；Leader 可自己验收，也可委派另一成员评审。
3. 成员反复无法完成时，Leader 可以接管实现。接管必须留下原因、交接范围和后续验收记录；历史不能被覆盖，Leader 的最终责任不变。
4. 首个默认团队是 `fable-core`：Leader 为 `Claude Fable 5 / high`；成员为 `Sonnet / xhigh`、`Opus / high`、`Luna / high-fast`、`Sol / xhigh`。Claude 系列必须由 `claude` 启动，Luna/Sol 必须由 `codex` 启动；`agent` 不属于这个默认团队，只保留给后续明确配置的 Cursor 自有 Grok 4.6 与 Composer 2.5。
5. 团队档案放在 `~/.amux/teams/`；工作区只保存所选团队的引用。任务与审计仍按工作区隔离。

## Goal

- [x] **TEAM-001** — 可保存的团队档案：在 `~/.amux/teams/<id>.toml` 保存、校验和读取团队；每个档案必须恰有一位 Leader 和至少一位成员，成员需记录模型、推理强度、速度偏好与职责。`amux team init|list|show|use|current` 可初始化 `fable-core`、查看团队，并把现有团队绑定到当前工作区 `~/.amux/workspaces/<slug>/team.toml`；不存在或无效团队不得覆盖绑定。同步产品、架构、README 与命令帮助。此 Goal 不启动模型、不猜测 CLI 模型参数，亦不把群聊伪装成任务系统。
  - 前置:WS-012。
  - 验证:`uv run ruff check .`;`uv run pytest tests/test_teams.py tests/test_global_config.py tests/test_workspace.py tests/test_console_app.py tests/test_engineering_skeleton.py -q`(41 passed);`uv run amux team --help`。
  - 证据:`src/team/model.py` 严格校验唯一 Leader、成员与模型/强度/速度/职责；`src/team/store.py` 写入 `fable-core`;`src/team/binding.py` 先校验团队再更新工作区引用；`src/team/cli.py` 提供五个命令；`tests/test_teams.py` 覆盖默认档案、无效团队不覆盖原绑定及命令全链路。另以临时 `AMUX_HOME` 实测 `init → use → current → show`，输出 Fable Leader 和四名成员。

- [ ] **TEAM-002** — 任务账本与责任流：为工作区建立不可覆盖的任务事件流，覆盖 Leader 建任务/拆解/派工、成员进展/阻塞/提交证据、评审通过/退回、Leader 验收/重新分派/接管与向 human 汇报；任务看板和详情页以账本为主，群聊仅显示关联沟通。成员不能最终结项，任何最终完成都能追到负责 Leader。
  - 前置:TEAM-001。
  - 进行中:前置 TEAM-001 已完成，待认领。

- [x] **TEAM-003** — Fable 团队运行时激活：团队成员档案可保存经验证的本机启动命令与参数；默认 `fable-core` 固定映射 Fable/Sonnet/Opus 到 `claude`，Luna/Sol 到 `codex`，不生成 `agent` 成员。`amux team activate [team-id]` 先校验全部运行器，再绑定团队、仅关闭当前工作区旧名册的已启用成员、写入 `~/.amux/workspaces/<slug>/members.toml` 并拉起新名册。Luna 使用 Codex 的 `priority` 服务档以兑现 `high-fast`；Sol 使用 `xhigh`。激活失败不得改写原有绑定或成员名单。
  - 前置:TEAM-001。
  - 验证:`uv run ruff check .`(通过);`uv run pytest tests/test_teams.py tests/test_team_activation.py tests/test_workspace_members.py tests/test_global_config.py tests/test_workspace_session.py tests/test_console_app.py tests/test_engineering_skeleton.py -q`(37 passed)。
  - 证据:`src/team/store.py` 在 `~/.amux/teams/fable-core.toml` 保存三条 `claude` 与两条 `codex` 适配；`src/team/activation.py` 先校验运行器、再只结束旧有效名册并投影新 `members.toml`；`tests/test_team_activation.py` 覆盖无 `agent`、工作区隔离与失败不改状态。实机在 `proj_fppt` 运行 `amux team init --force`、`amux team activate fable-core`：旧 `claude`/`codex` 均关闭，`fable`、`sonnet`、`opus`、`luna`、`sol` 均启动；`tmux list-sessions` 和终端抓取确认 Fable high、Luna high fast 已就绪。

- [x] **TEAM-004** — Claude 成员的可回滚终端适配：团队成员运行时适配支持并严格校验 `env`，激活时完整投影到工作区名册；默认 `fable-core` 的 Fable/Sonnet/Opus 设置 `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`，使 tmux 能保留 Claude 对话回滚区，Luna/Sol 不受影响。控制台帮助与直连提示明确 Mac 的 `Fn+↑/Fn+↓` 即 PgUp/PgDn，并说明空 Delete 只删除成员当前未提交草稿、不能修改已提交消息；保留现有滚轮和快捷键。补自动化测试、文档、真实 Claude pane 与实际画面验证。
  - 前置：TEAM-003、CON-015。
  - 验证（Codex，2026-08-23）：`uv run --offline ruff check src/team src/console/app.py src/console/help.py tests/test_teams.py tests/test_team_activation.py tests/test_console_compose.py tests/test_console_keyboard.py` 通过；`uv run --offline pytest tests/test_teams.py tests/test_team_activation.py tests/test_workspace_members.py tests/test_console_compose.py tests/test_console_keyboard.py -q` 为 `30 passed in 9.78s`；运行 `uv run --offline python -m qa.visual --goal TEAM-004 --scene claude-scrollback --size 120x30 --fixture controls --keys Down,Tab,Tab,PageUp` 及对应 80×24 场景。
  - 证据：`src/team/model.py` 严格解析成员 `env`，`src/team/store.py` 只给默认团队的三个 Claude 成员设置 `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`，`src/team/activation.py` 将环境完整投影为 `Member` 并由现有工作区序列化保存，Luna/Sol 环境保持空；测试覆盖默认值、非法类型、roster 投影与落盘重读。实机 Fable 在切换前为 `alternate_on=1, history_size=0`，执行 `/tui default` 并跳过反馈后为 `alternate_on=0, history_size=42`，随后增长到 90；`capture-pane -S` 可读到早于当前屏的文本。真实 Sonnet pane 另以未提交 `XYZ` 验证同一 `BSpace` 后变为 `XY`、光标从第 5 列回到第 4 列，证明空 Del 透传有效；截图里的既有提问当时光标在空草稿第 2 列，是已提交记录，按删除键不应改写。当前 `proj_fppt/members.toml` 已给三个 Claude 成员补同一环境，后续重启继续生效。
  - 视觉自验证：`tests/baseline/team-004-claude-scrollback-120x30.txt`/`.ansi` 与 `tests/baseline/team-004-claude-scrollback-min-80x24.txt`/`.ansi` 都显示 `↑ 回看 claude 的 tmux 历史(start=-10)` 和三条较早记录，证明 PgUp 在输入框聚焦时仍进入成员回滚区；直连提示明确显示「空Del删草稿」「Fn+↑↓回看」，120×30 与 80×24 下中文、输入框和三栏边界均无截断错位。画面使用 controls 夹具，没有向真实成员发送测试键。
