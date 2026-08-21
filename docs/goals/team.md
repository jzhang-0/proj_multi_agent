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

- [ ] **TEAM-003** — Fable 团队运行时激活：团队成员档案可保存经验证的本机启动命令与参数；默认 `fable-core` 固定映射 Fable/Sonnet/Opus 到 `claude`，Luna/Sol 到 `codex`，不生成 `agent` 成员。`amux team activate [team-id]` 先校验全部运行器，再绑定团队、仅关闭当前工作区旧名册的已启用成员、写入 `~/.amux/workspaces/<slug>/members.toml` 并拉起新名册。Luna 使用 Codex 的 `priority` 服务档以兑现 `high-fast`；Sol 使用 `xhigh`。激活失败不得改写原有绑定或成员名单。
  - 前置:TEAM-001。
  - 处理登记:Codex，2026-08-22，`team-003-codex`。
  - 进行中:依据 human 对 CLI 边界的补充，正在实现并准备激活 `proj_fppt`。
