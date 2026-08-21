# 发布工程(REL)

`amux` 的源码开发安装与公开发行是两条路径：仓库内继续用 `uv run` 和历史兼容入口；发布候选必须形成不依赖源码仓库的 sdist/wheel，并在干净目录安装验证。

## Goal

- [ ] **REL-001** — 可独立安装的发行包：PyPI 发行名使用尚未占用的 `amux-team`，对外命令仍为 `amux`，历史 `console`/`roster` 入口暂时保留；版本只维护一处，补齐不涉及法律选择的项目元数据。将运行必需的默认名册和群聊协议作为受同步测试保护的包内资源，源码开发继续以根目录 `roster.toml` / `AGENTS.md` 为权威，wheel 环境自动回退包内资源。增加 `uv build --no-sources`、制品内容检查、源码仓库外隔离安装 smoke，以及 tag 触发的 GitHub Actions 测试/构建/PyPI Trusted Publishing 工作流；同步 README 和架构。此 Goal 不上传制品、不创建远端仓库、不替 human 选择许可证。
  - 前置:CON-013、ROS-006、WS-011。
  - 处理登记:Codex，2026-08-22，`rel-001-codex`。
  - 进行中:正在消除 wheel 对源码根资源的依赖并建立可重复发布链路。
