# AGENTS.md — 总控台仓库入口

本文件只提供仓库入口、运行时提示词入口和少量硬规则。产品、架构和 Goal 统一维护在 `docs/`,不在这里复述。

## 开工前

先读 [文档索引](docs/README.md),再按任务进入权威文档:产品范围看 [产品定义](docs/product/product.md),模块边界与技术栈看 [架构决策](docs/architecture/architecture.md),工程任务与完成契约看 [Goal 总索引](docs/goals/README.md)。

## amux 运行时提示词

成员启动提示的唯一事实来源是 [`src/amux_runtime/prompts/`](src/amux_runtime/prompts/README.md)。公共规则、Leader 职责和普通成员职责分别维护在三个 Markdown 文件中;Python、`AGENTS.md`、团队档案和名册不得复制正文。运行时根据团队档案投影出的角色直接读取并拼装这些文件。

## 工作规则(多 AI 协作开发)

- 以 `docs/goals/` 中的 Goal 为工作与验收单位;完成契约、执行顺序、已知陷阱见 [Goal 总索引](docs/goals/README.md)。
- 每次只做一个 Goal:先在该 Goal 下写 `处理登记`(模型、开始时间、分支),新开 worktree,分支名 `<goal-id小写>-<模型>`(如 `bus-002-claude`)。
- 做完 merge 进 `main`,没报错就算过;冲突自己解决;合入后删分支、删登记,补 `验证` 与 `证据`。
- 只要 Goal 未被认领且前置满足就可以做,先标记认领。
- 并发开发多,不要频繁测试;必要时测,能轻量就轻量,不跑全量。
- Goal 描述中的枚举是完整清单;未完成保留 `[ ]` 并写 `进行中`。
- 界面类变更必须实际截取画面确认(Goal 总索引「视觉自验证」),测试通过不能替代看图。
- Python 一律 `uv run`(系统 python 是 3.8,不满足项目要求);ruff 违规视为错误。
- 测试使用临时目录,不碰仓库根 `bus/` 运行时数据。

## 验证与收尾

```bash
uv run ruff check .
uv run pytest <单个测试文件> -q   # 按需,轻量
uv run python -m qa.smoke        # 总线端到端(QA-002 落地后)
```

实际运行过的命令和结果才能写入 Goal 证据。
