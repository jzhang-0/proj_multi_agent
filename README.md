# proj_multi_agent — 总控台

一台机器上多个 AI CLI(claude / codex / cursor / agy)组成一个"群":互相 @ 协作,人从一个总控台里看到一切、指挥一切。产品形态与硬指标见 [产品定义](docs/product/product.md)。

## 现状:v0 总线(可用)

```bash
./start.sh          # 拉起全部成员,本窗口变成群聊记录(hub)
./msg claude 写一个fizzbuzz 写完让cursor review 通过后向我汇报
./msg --ask claude 这个改动是否已经通过测试
./msg --reply <ask-id> 已通过,验证命令见日志
tmux attach -t codex   # 围观某个成员(Ctrl-b d 退出)
./start.sh stop     # 收工
```

- 收件人名字 = tmux 会话名;`human` 保留给人,消息只显示在 hub 窗口。
- 消息单行注入;成员正忙时排进其输入队列。全部流量留档 `bus/log.jsonl`。
- `--ask` 默认阻塞等待关联回复 10 分钟;收件人按投递消息里的 `--reply <ask-id>` 指引答复。可用 `--timeout <秒>` 缩短等待。
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

投递延迟实测:`uv run python -m bus.bench`(起临时 tmux 会话跑 `cat` 当收件人,打印 min/P50/P95/max 并按 P95 < 200ms 判定;`--fake` 只量总线自身调度)。

批 1 收口冒烟:`uv run python -m qa.smoke`(假成员 `cat` 窗格 → 入队 → 投递 → 窗格收到,打印入队到上屏延迟;用临时目录,不碰仓库根 `bus/`)。

消息总线模块是 `src/bus/`:消息 schema v1(`to/from/text/ts` 必备,`id/kind/replyTo` 可选,未知字段原样保留)、文件队列、死信目录、投递循环。bus 运行时根目录默认是仓库根 `bus/`,可用环境变量 `BUS_ROOT` 或 `BusPaths.resolve(root)` 重定向(测试一律指向临时目录)。

tmux 控制层是 `src/tmuxctl/`:启动时探测 tmux ≥ 3.2,并把 `has-session` / `new-session` / `kill-session` / `send-keys` / `capture-pane` / `list-panes` 收口为类型化 API;其他模块不要直接拼 tmux 命令。

成员名册是 `roster.toml`(由 `src/roster` 加载校验)。`./start.sh` 是读名册的薄入口。各成员免弹窗参数与残留弹窗写在 `roster.toml` 注释里;claude 的 `./msg` 白名单在 `.claude/settings.json`。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | AI 入口:群聊协议 + 工作规则 |
| `docs/` | 产品定义、架构决策、Goal 清单 |
| `pyproject.toml` / `uv.lock` | Python、依赖、pytest、ruff 与命令入口配置 |
| `src/` / `tests/` | 应用源码与自动化测试(`src/bus`、`src/console`、`src/tmuxctl`、`src/qa`、`src/roster`) |
| `hub.py` `msg` `start.sh` | v0 总线入口(`start.sh` 现读 `roster.toml`) |
| `roster.toml` | 成员名册(名字、启动命令、开场白、启用与否) |
| `bus/` | 运行时数据(gitignore) |
| `reference/pi-extensions/` | 参考实现:pi 的 talk 扩展(只读,防环策略的出处) |
