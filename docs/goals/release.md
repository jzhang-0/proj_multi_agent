# 发布工程(REL)

`amux` 的源码开发安装与公开发行是两条路径：仓库内继续用 `uv run` 和历史兼容入口；发布候选必须形成不依赖源码仓库的 sdist/wheel，并在干净目录安装验证。

## Goal

- [x] **REL-001** — 可独立安装的发行包：PyPI 发行名使用尚未占用的 `amux-team`，对外命令仍为 `amux`，历史 `console`/`roster` 入口暂时保留；版本只维护一处，补齐不涉及法律选择的项目元数据。将运行必需的默认名册和群聊协议作为受同步测试保护的包内资源，源码开发继续以根目录 `roster.toml` / `AGENTS.md` 为权威，wheel 环境自动回退包内资源。增加 `uv build --no-sources`、制品内容检查、源码仓库外隔离安装 smoke，以及 tag 触发的 GitHub Actions 测试/构建/PyPI Trusted Publishing 工作流；同步 README 和架构。此 Goal 不上传制品、不创建远端仓库、不替 human 选择许可证。
  - 前置:CON-013、ROS-006、WS-011。
  - 验证（Codex，2026-08-23）：`uv run ruff check .` 通过；合并后 `uv run pytest -q` 为 `474 passed in 32.96s`；`uv run pytest tests/test_release_package.py -q` 为 `4 passed`；`uv run python -m qa.release --out-dir dist` 生成并验证 `amux_team-0.1.0-py3-none-any.whl` 与 `amux_team-0.1.0.tar.gz`。
  - 证据：`src/amux_runtime/` 提供受测试约束的名册/协议快照，`src/qa/release.py` 在源码仓库外离线安装 wheel 并验证版本、名册与默认团队；`.github/workflows/release.yml` 将构建与 OIDC 发布 job 分离，手工运行发布 TestPyPI，版本一致的 `v*` 标签发布 PyPI；发布操作说明见 `docs/releasing.md`。未上传制品、未创建远端、未选择许可证。

- [x] **REL-002** — 严格联网发行 smoke：默认在源码仓库外创建临时虚拟环境和全新 uv 缓存，从索引安装 wheel 及其完整依赖，验证声明依赖可导入、命令入口与包内资源可用；保留显式离线 payload smoke 供断网诊断，但 GitHub 发布工作流必须使用联网严格模式。同步发行说明并增加自动化测试。
  - 前置:REL-001。
  - 验证（Codex，2026-08-23）：`uv run ruff check .` 通过；合并后 `uv run pytest -q` 为 `476 passed in 49.55s`；通过 Surge `PyPI → NX-HK01` 执行 `uv run python -m qa.release --out-dir dist`，全新缓存联网安装 wheel 与完整依赖并通过命令/资源 smoke；`uv run python -m qa.release --out-dir dist --skip-build --offline-smoke` 也通过并明确标注未验证依赖。
  - 证据：`src/qa/release.py` 默认创建独立 `UV_CACHE_DIR`、正常解析依赖并导入 `textual`/`watchfiles`，仅显式 `--offline-smoke` 才添加 `--offline --no-deps`；`tests/test_release_package.py` 覆盖严格/离线参数分支并禁止发布 workflow 使用离线选项；`docs/releasing.md` 与架构完成契约已同步。

- [x] **REL-003** — MIT 发行许可：按 human 选择加入标准 MIT `LICENSE`，版权主体使用仓库 Git 身份 `jzhang-0`；以 SPDX 表达式和显式 license file 配置项目元数据，确保 wheel/sdist 携带许可证且 wheel 元数据声明 `License-Expression: MIT`，同步 README 与发行说明并增加制品检查测试。
  - 前置:REL-001。
  - 验证（Codex，2026-08-23）：`uv run ruff check src/qa/release.py tests/test_release_package.py` 通过；`uv run pytest tests/test_release_package.py -q` 为 `6 passed`；`uv run python -m qa.release --out-dir dist --offline-smoke` 构建并验证 wheel/sdist 的许可证文件与元数据。
  - 证据：根 `LICENSE` 是标准 MIT 正文并署名 `jzhang-0`；`pyproject.toml` 使用 SPDX `license = "MIT"` 和 `license-files = ["LICENSE"]`；`src/qa/release.py` 检查 wheel 的 `License-Expression`/`License-File` 以及 wheel/sdist 内许可证路径，README 与发行说明已同步。

- [ ] **REL-004** — 首次公开发布：补齐 GitHub 项目 URL 元数据并验证制品，推送 `main`；配置 GitHub `testpypi`/`pypi` environments 与两站 Trusted Publisher，发布并验证 TestPyPI，再以 `v0.1.0` 标签发布正式 PyPI，最后从正式索引全新安装并验证 `amux --version`。
  - 前置:REL-002、REL-003。
  - 处理登记:Codex，2026-08-23，`rel-004-codex`。
  - 进行中:正在补齐远端元数据并执行首次 TestPyPI/PyPI 发布。
