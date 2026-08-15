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
- [ ] **QA-004** — 视觉自验证流程落地:文档化「console 跑在 tmux 里 → `capture-pane -p -e` 截画面 → 读图/读文本判断」的步骤和判定清单(对齐、配色、状态徽标、中文宽度),CON 卷的证据必须引用实际截取物路径。
  - 前置:CON-002。
- [ ] **QA-005** — 多成员协作实测:四个真实成员经总控台完成一次「派活 → 协作 → 汇报」全流程,时间线、状态、控制操作全程可用;过程记录(截取物 + 审计日志片段)存档作为证据。
  - 前置:CON-007、ROS-002、QA-002。
