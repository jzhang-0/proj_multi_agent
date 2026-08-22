# 打包与发布

PyPI 发行名为 `amux-team`，安装后的主要命令仍是 `amux`。PyPI 上的 `amux` 已属于另一项目，不得尝试覆盖。源码仓库内继续保留 `console` / `roster` 兼容入口。

项目采用 MIT License，版权主体为 `jzhang-0`；构建检查会验证许可证正文同时进入 wheel 和 sdist。

## 发布前检查

发布机需要 Python ≥ 3.11 和 `uv`；终端运行还要求 `tmux ≥ 3.2`。Claude Code、Codex CLI 和 Cursor Agent 是用户自行安装、登录的外部运行器，不进入 Python 制品。

从仓库根执行：

```bash
uv sync --locked
uv run ruff check .
uv run pytest -q
uv run python -m qa.release --out-dir dist
```

最后一条会执行 `uv build --no-sources`，检查 wheel/sdist 的资源、入口与依赖元数据，然后在源码仓库外创建临时虚拟环境和全新 uv 缓存，从包索引安装 wheel 及其完整依赖，验证依赖导入、版本、包内名册和默认团队。它不上传任何文件。

断网诊断时可追加 `--offline-smoke`，只离线安装 wheel payload 且跳过依赖解析；该模式不能作为正式发布证据，GitHub 发布 workflow 始终使用默认联网严格模式。

## TestPyPI 与 PyPI

远端仓库建立后，在 TestPyPI 和 PyPI 分别创建名为 `amux-team` 的项目或 pending trusted publisher，并配置 GitHub environments `testpypi` / `pypi`：

- 手工运行 `release` workflow：构建通过后发布到 TestPyPI。
- 推送 `v<pyproject版本>` tag：版本匹配检查和构建通过后发布到正式 PyPI；标签与 `pyproject.toml` 不一致时 workflow 会拒绝发布。

workflow 使用 OIDC Trusted Publishing，不保存长期 API token。正式 tag 创建前，必须确认 `pyproject.toml` 的版本与 tag 一致；PyPI 版本不可覆盖。

## 用户安装

发布后：

```bash
uv tool install amux-team
amux --version
amux
```

源码开发安装仍可执行 `./install-amux.sh`。它只是指向当前 checkout 的 shim，不属于公开制品。

## 仍需 human 补充

仓库目前没有远端地址，因此项目主页、问题追踪链接需在建好远端后补入。
