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

- [x] **REL-004** — 首次公开发布：补齐 GitHub 项目 URL 元数据并验证制品，推送 `main`；配置 GitHub `testpypi`/`pypi` environments 与两站 Trusted Publisher，发布并验证 TestPyPI，再以 `v0.1.0` 标签发布正式 PyPI，最后从正式索引全新安装并验证 `amux --version`。
  - 前置:REL-002、REL-003。
  - 验证（Codex，2026-08-23）：`uv run ruff check .` 通过；`uv run pytest tests/test_release_package.py -q` 为 `6 passed`；隔离构建验证生成 `amux_team-0.1.0-py3-none-any.whl` 与 `amux_team-0.1.0.tar.gz`。GitHub `release` 手工运行 #1 成功发布 TestPyPI；`v0.1.0` 触发的运行 #2 首次被 `test_watch_mode_picks_up_new_message` 的已知后台清理时序波动挡住（`1 failed, 475 passed`），保留同一标签重跑后构建、测试与 PyPI OIDC 上传全部通过。
  - 证据：TestPyPI 与 PyPI 的 JSON 索引均返回 `amux-team==0.1.0` 的 wheel/sdist；正式 [PyPI 项目](https://pypi.org/project/amux-team/0.1.0/) 与 [GitHub 发布流水线](https://github.com/jzhang-0/proj_multi_agent/actions/runs/32594800087) 可公开复核。最终使用 `uv tool install --force --no-cache --no-config --no-sources --default-index https://pypi.org/simple 'amux-team==0.1.0'` 替换旧源码 shim，`~/.local/bin/amux` 指向 uv tool 环境且 `amux --version` 输出 `amux 0.1.0`。

- [x] **REL-005** — 可重复安装的源码开发入口：`./install-amux.sh dev` 只生成 `amux-dev`，不覆盖 PyPI 管理的 `amux`；开发入口指向当前源码 checkout，默认复用正式版的 `~/.amux`，让已保存并激活的团队在开发版中可见，只有显式设置 `AMUX_DEV_HOME` 时才使用隔离状态目录；提供卸载命令、自动化测试与 README 说明。
  - 前置：REL-004、TEAM-003。
  - 验证（Codex，2026-08-23）：`bash -n install-amux.sh`、`uv run --offline ruff check tests/test_dev_installer.py` 通过；`uv run --offline pytest tests/test_dev_installer.py -q` 为 `2 passed`。从 main 执行 `./install-amux.sh dev` 后，在 `/Users/jzhang/Downloads/proj_fppt` 运行 `amux-dev workspace current`、`amux-dev team current`、`amux-dev member list`，识别到 `proj_fppt`、`fable-core` 和 Fable/Sonnet/Opus/Luna/Sol；`amux-dev --headless --once` 后以 `tmux list-sessions` 确认五个 `<成员>@proj_fppt` 会话全部存在。
  - 证据：`install-amux.sh dev|uninstall-dev` 生成和卸载独立 `amux-dev`，生成物不设置默认 `AMUX_HOME`，显式 `AMUX_DEV_HOME` 时才映射为隔离状态；`tests/test_dev_installer.py` 使用假 `uv` 验证共享/隔离两条路径、源码参数透传、卸载以及 PyPI `amux` 不被覆盖；README、架构和发行说明同步开发工作流。实机 `~/.local/bin/amux-dev` 已从 main 重新生成，不再引用旧的 `~/.amux-dev`。

- [ ] **REL-006** — `qa.release` 隔离冒烟自动启动 Web 并断言实时握手：当前脚本只装包、校验包内静态资源并跑 CLI 子命令，不启动 `amux web`，`/api/v1/stream` 的 101 握手由人工旁跑(T-024)。把「在隔离环境用该环境解释器起 `amux web`(真实 pid)→首页 200→原生 WebSocket 握手 101→干净停服」并入 `qa.release`(联网与 `--offline-smoke` 两种模式都覆盖，offline 下仍起服务、仍连 /api/v1/stream，结果记为「未验证依赖解析」而非「握手通过」；若环境无 WS 实现导致 404 必须明确报错，不得静默降级为 PASS)，让 T-013 那类 WS 依赖缺失由脚本而非人工拦住。tests/test_release_package.py 补对应断言。
  - 前置:WEB-009。

- [ ] **REL-007** — 发布 v0.2.0（Web 控制台首版）：human 2026-08-30 要求在 T-025/T-026 修复验收后推送 GitHub 并发布新版 amux。内容：pyproject `version` 升 0.2.0；补发行说明(README 或 CHANGELOG：WEB-001～011 能力、`amux web` 用法、`uvicorn[standard]` 依赖、安全边界、已知限制含 WEB-012/REL-006 未做)；本地复跑 `uv run ruff check .`、`npm --prefix web run verify`、`uv build`、`uv run python -m qa.release`；确认 release.yml 前端构建在 `uv build` 之前且 PyPI job 只用 build 产物；推送 `main` 与 `v0.2.0` 标签触发发布；从正式 PyPI 全新安装 `amux-team==0.2.0` 验证 `amux --version` 与 `amux web` 首页/SPA 可用。前置：T-025、T-026 验收。
  - 前置:REL-004、WEB-009。
