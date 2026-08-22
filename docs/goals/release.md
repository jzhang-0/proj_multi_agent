# 发布工程(REL)

`amux` 的源码开发安装与公开发行是两条路径：仓库内继续用 `uv run` 和历史兼容入口；发布候选必须形成不依赖源码仓库的 sdist/wheel，并在干净目录安装验证。

## Goal

- [x] **REL-001** — 可独立安装的发行包：PyPI 发行名使用尚未占用的 `amux-team`，对外命令仍为 `amux`，历史 `console`/`roster` 入口暂时保留；版本只维护一处，补齐不涉及法律选择的项目元数据。将运行必需的默认名册和群聊协议作为受同步测试保护的包内资源，源码开发继续以根目录 `roster.toml` / `AGENTS.md` 为权威，wheel 环境自动回退包内资源。增加 `uv build --no-sources`、制品内容检查、源码仓库外隔离安装 smoke，以及 tag 触发的 GitHub Actions 测试/构建/PyPI Trusted Publishing 工作流；同步 README 和架构。此 Goal 不上传制品、不创建远端仓库、不替 human 选择许可证。
  - 前置:CON-013、ROS-006、WS-011。
  - 验证（Codex，2026-08-23）：`uv run ruff check .` 通过；合并后 `uv run pytest -q` 为 `474 passed in 32.96s`；`uv run pytest tests/test_release_package.py -q` 为 `4 passed`；`uv run python -m qa.release --out-dir dist` 生成并验证 `amux_team-0.1.0-py3-none-any.whl` 与 `amux_team-0.1.0.tar.gz`。
  - 证据：`src/amux_runtime/` 提供受测试约束的名册/协议快照，`src/qa/release.py` 在源码仓库外离线安装 wheel 并验证版本、名册与默认团队；`.github/workflows/release.yml` 将构建与 OIDC 发布 job 分离，手工运行发布 TestPyPI，版本一致的 `v*` 标签发布 PyPI；发布操作说明见 `docs/releasing.md`。未上传制品、未创建远端、未选择许可证。

- [ ] **REL-002** — 严格联网发行 smoke：默认在源码仓库外创建临时虚拟环境和全新 uv 缓存，从索引安装 wheel 及其完整依赖，验证声明依赖可导入、命令入口与包内资源可用；保留显式离线 payload smoke 供断网诊断，但 GitHub 发布工作流必须使用联网严格模式。同步发行说明并增加自动化测试。
  - 前置:REL-001。
  - 处理登记:Codex，2026-08-23，`rel-002-codex`。
  - 进行中:正在把网络恢复后的完整依赖安装纳入发布候选完成契约。
