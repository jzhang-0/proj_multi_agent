# proj_multi_agent — 总控台

一台机器上多个 AI CLI(claude / codex / cursor / agy)组成一个"群":互相 @ 协作,人从一个总控台里看到一切、指挥一切。产品形态与硬指标见 [产品定义](docs/product/product.md)。

## 现状:v0 总线(可用)

```bash
./start.sh          # 拉起全部成员,本窗口变成群聊记录(hub)
./msg claude 写一个fizzbuzz 写完让cursor review 通过后向我汇报
tmux attach -t codex   # 围观某个成员(Ctrl-b d 退出)
./start.sh stop     # 收工
```

- 收件人名字 = tmux 会话名;`human` 保留给人,消息只显示在 hub 窗口。
- 消息单行注入;成员正忙时排进其输入队列。全部流量留档 `bus/log.jsonl`。
- 各成员的免权限弹窗配置见 `start.sh` 与 [roster Goal 卷](docs/goals/roster.md)。

## 开发中:总控台

这是一个多 AI 协作开发的仓库。开发者(即群成员)从 [AGENTS.md](AGENTS.md) 入口开工,任务全部在 [docs/goals/](docs/goals/README.md)。

工程环境由 `uv` 管理,要求 Python ≥ 3.11:

```bash
uv sync
uv run console
uv run pytest -q
uv run ruff check .
```

当前 `console` 命令用于验证工程入口;完整 TUI 按 CON 卷 Goal 逐步落地。系统 Python 版本不满足要求时也不要绕过 `uv run`。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | AI 入口:群聊协议 + 工作规则 |
| `docs/` | 产品定义、架构决策、Goal 清单 |
| `pyproject.toml` / `uv.lock` | Python、依赖、pytest、ruff 与命令入口配置 |
| `src/` / `tests/` | 应用源码与自动化测试(`src/console`、`src/tmuxctl`) |
| `hub.py` `msg` `start.sh` | v0 总线(保持可用,逐步变薄入口) |
| `bus/` | 运行时数据(gitignore) |
| `reference/pi-extensions/` | 参考实现:pi 的 talk 扩展(只读,防环策略的出处) |
