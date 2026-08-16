# 质量与工程(QA)

约束回顾:一台机器上大量并发开发,**测试要轻、要少跑全量**;测试一律用临时目录,不碰仓库根 `bus/`。

- [x] **QA-001** — 工程骨架:`pyproject.toml`(uv 管理,Python ≥ 3.11)、`src/` 布局、pytest + ruff 配置、`uv run console` / `uv run pytest -q` 可用;ruff 违规视为错误。
  - 前置:无。
  - 验证:`uv run pytest -q && uv run ruff check . && uv run console`
  - 证据:`pyproject.toml`、`uv.lock`、`src/console/`、`tests/test_engineering_skeleton.py`;2026-08-16 在 `main` 实测 3 passed、ruff 全绿、console 正常运行。
- [x] **QA-002** — 端到端冒烟:一条命令(`uv run python -m qa.smoke`)完成「起假成员窗格(cat)→ msg 入队 → 投递 → 窗格收到 → 清理」,输出投递延迟;这是批 1 的收口命令。
  - 前置:BUS-006、TMX-002。
  - 验证:`uv run ruff check . && uv run python -m qa.smoke && uv run pytest tests/test_qa_smoke.py -q`
  - 证据:`src/qa/smoke.py`(隔离 socket + 临时 `BUS_ROOT` + `KeyInjector` 投递);2026-08-16 实测 `qa.smoke: ok enqueue→pane 35.1ms mode=watch`;`tests/test_qa_smoke.py` 1 passed。
- [x] **QA-003** — 防环策略单元测试:去重、限频、熔断、超长、human 豁免各分支覆盖;不依赖真实 tmux。
  - 前置:BUS-002、BUS-003、BUS-004。
  - 验证:`uv run pytest tests/test_bus_policy_matrix.py -q && uv run ruff check .`
  - 证据:`tests/test_bus_policy_matrix.py` 直接调用 `OutboundPolicy`,以 5 个独立测试覆盖复读、第 9 条限频、50 条积压、32KB 超长与 human 发送豁免,未导入或启动 tmux;2026-08-16 在 `main` 实测 5 passed、ruff 全绿。
- [x] **QA-004** — 视觉自验证流程落地:文档化「console 跑在 tmux 里 → `capture-pane -p -e` 截画面 → 读图/读文本判断」的步骤和判定清单(对齐、配色、状态徽标、中文宽度),CON 卷的证据必须引用实际截取物路径。
  - 前置:CON-002。
  - 验证:`uv run ruff check . && uv run pytest tests/test_visual_evidence.py -q && uv run python -m qa.visual --goal QA-004 --scene tool --size 100x26 --say 视觉自验证流程跑通了 --keys Down`
  - 证据:[视觉自验证](../quality/visual-check.md)——三步流程(截画面 / 看图判定 / 写证据)与六组判定清单(对齐、配色、状态徽标、中文宽度、尺寸、退出安全),含手动等价命令与"审美问题标需人工交 human"的出口;`src/qa/visual.py` 把流程固化成 `uv run python -m qa.visual`(临时 bus 根 + 临时会话、等界面画完再截、`-p` 与 `-p -e` 各存一份到 `tests/baseline/`、跑完核对原有 tmux 会话没被带走);`tests/test_visual_evidence.py` 3 passed,钉死"每个已完成的 CON Goal 必须引用真实存在的 `tests/baseline/` 截取物"以及清单六节都在;`docs/README.md` 与 Goal 总索引已加索引。
  - 实测截取物:`tests/baseline/qa-004-tool-100x26.txt` 与同名 `.ansi`(100×26,发一条消息 + 按 Down 选中 claude:三栏分隔线贯通、右侧详情栏展开、中英文混排行右边框不错位);跑完 `tmux ls` 六个原有会话一个不少。
- [x] **QA-005** — 多成员协作实测:四个真实成员经总控台完成一次「派活 → 协作 → 汇报」全流程,时间线、状态、控制操作全程可用;过程记录(截取物 + 审计日志片段)存档作为证据。
  - 前置:CON-007、ROS-002、QA-002。
  - 验证:`uv run python -m qa.collab verify`(`message-events=20 controls=1`);`uv run pytest tests/test_qa_collab.py tests/test_visual_evidence.py -q`(13 passed);`uv run ruff check .`。真实流程使用 160×40 `qa005-console`，退出后 `tmux list-sessions -F '#{session_name}\t#{session_created}'` 确认 `claude=1786827034`、`codex/cursor/agy=1786827035` 与启动前一致。
  - 证据:[协作实测流程](../quality/collaboration-check.md);`tests/baseline/qa-005-collaboration-160x40.txt`/`.ansi` 同屏显示四张真实成员卡、`human→claude`派活、Claude 向 Codex/Cursor/Agy 分工、三路回报、Claude 最终汇总与 `[控制] ✓ interrupt cursor`；`tests/evidence/qa-005-audit.jsonl` 存档 20 条带 `QA005-20260816-A` 的标准 `deposit/deliver` 事件和 1 条 `control/interrupt/changed=true`。`src/qa/collab.py` 与 `tests/test_qa_collab.py` 离线钉死 8 条必需有向消息边、四人名称、可见控制反馈及 ANSI 带色取证。
- [ ] **QA-006** — 多工作区端到端实测:两个真实项目各起一组成员,同时活着;各自派活、组内协作、回报,消息不串台;两组的审计日志各自独立;`amux workspace rm` 清掉其中一组后另一组不受影响。过程记录(截取物 + 两份审计日志片段 + 成员进程 cwd 的 `lsof` 取证)存档作为证据。
  - 前置:WS-001 至 WS-010 全部 `[x]`。
