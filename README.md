# amux

**在一台机器上，把多个 AI CLI 组织成一个可观察、可沟通、可接管的协作团队。**

amux 是一个基于 tmux 的终端总控台。Claude Code、Codex、Cursor Agent 等 CLI
仍在各自独立的真实终端里运行；amux 把它们集中到一个 TUI 中，负责成员状态、终端画面、
消息投递和审计记录。

当前版本为 **v0.1.0（Alpha）**，采用 [MIT License](LICENSE)。它适合愿意尝试本地多
Agent 工作流的开发者；任务账本、Leader 验收和接管流程仍在继续开发，当前范围与路线图见
[产品定义](docs/product/product.md) 和 [Goal 清单](docs/goals/README.md)。

## 它解决什么问题

同时使用多个 AI CLI 时，常见做法是开很多终端，再靠人记住谁在做什么、手动复制消息、来回
切窗口。amux 把这些操作收进一个界面：

- 每个成员拥有独立 tmux 会话，并在当前项目目录中工作；
- 一个界面查看群聊时间线、成员状态和成员终端画面；
- 可以向成员发消息、等待关联回复，或直接操作成员终端；
- 消息、投递结果和控制动作写入审计日志；
- 同一台机器可以登记多个项目，各自拥有独立成员和消息总线。

amux 不替代 Claude Code、Codex 等工具，也不会绕过它们的账号、登录或权限机制。它只负责把
已经能在本机运行的 CLI 组织起来。

## 三分钟上手

### 1. 准备环境

你需要：

- macOS 或其他 POSIX 环境；
- Python 3.11 或更高版本；
- [uv](https://docs.astral.sh/uv/)；
- tmux 3.2 或更高版本；
- 至少一个已经安装并登录的 AI CLI。

内置成员预设包括：

| amux 成员名 | 本机命令 | 对应工具 |
|---|---|---|
| `claude` | `claude` | Claude Code |
| `codex` | `codex` | OpenAI Codex CLI |
| `cursor` | `agent` | Cursor Agent |
| `agy` | `agy` | agy CLI |

没有某个 CLI 不影响使用，只添加你本机已有的成员即可。

### 2. 安装 amux

```bash
uv tool install amux-team
amux --version
```

PyPI 包名是 `amux-team`，安装后的命令名是 `amux`。升级时运行：

```bash
uv tool upgrade amux-team
```

### 3. 在项目中启动一个成员

进入你希望 AI 团队工作的项目目录：

```bash
cd /path/to/your-project
amux member add claude
roster up claude
amux
```

把 `claude` 换成你已安装的其他预设即可。第一次执行时，amux 会自动把当前目录登记为工作区；
运行时状态保存在 `~/.amux/`，不会向你的项目写入隐藏目录。

打开界面后：

- `Esc` 或 `F2` 返回群聊；
- 在成员画面中直接输入，可以操作该成员的终端；
- 以 `@成员名` 开头的内容会走消息总线；
- `F1` 查看完整快捷键；
- `q` 或 `Ctrl-C` 退出总控台，不会自动终止成员会话。

也可以从另一个终端直接派发消息：

```bash
amux msg claude "检查当前项目并告诉我最值得先修的问题"
amux msg --ask claude "测试是否已经通过？"
```

## 常用操作

### 管理工作区

```bash
amux workspace add .        # 显式登记当前项目；通常可省略
amux workspace list         # 查看全部工作区
amux workspace current      # 查看当前目录属于哪个工作区
amux workspace rm <slug>    # 取消登记，不删除项目文件
```

### 管理成员

```bash
amux member add codex
amux member list
roster up codex
roster restart codex
roster down codex
amux member rm codex
```

`member rm` 只从工作区名册移除成员，不会关闭仍在运行的会话；需要时先执行
`roster down <成员名>`。

### 使用默认团队

如果本机已经安装了默认团队需要的 Claude 和 Codex CLI，可以初始化并激活内置的
`fable-core` 团队档案：

```bash
amux team init
amux team show fable-core
amux team activate fable-core
amux team current
```

团队档案记录 Leader、成员、模型偏好和职责。激活后，amux 会把团队成员投影到当前工作区名册。

### 设置全局默认值

```bash
amux config init
amux config show
```

这会创建 `~/.amux/config.toml`，其中包含默认成员、自动拉起成员和主题设置。默认配置会尝试
启动全部内置成员，因此请先确认相应 CLI 已安装；也可以直接编辑该文件，只保留自己需要的成员。

## 界面与数据

amux 的主界面由“会话列表 + 主画面 + 底部输入框”组成。选中群聊时，主画面显示时间线；
选中成员时，主画面显示该成员的终端镜像。成员画面支持滚动、直接输入，以及 F5–F8 的打断、
终止、重启和全屏接管操作。

每个工作区的运行时数据默认位于：

```text
~/.amux/workspaces/<slug>/
├── workspace.toml   # 项目路径
├── members.toml     # 当前工作区成员
├── team.toml        # 当前绑定的团队
└── bus/             # 消息队列、正文和审计日志
```

项目之间的队列和成员会话相互隔离。成员 tmux 会话名采用 `<成员>@<工作区>`，因此同一个成员
可以同时服务多个项目。

## 从源码运行

如果你想参与开发：

```bash
git clone https://github.com/jzhang-0/proj_multi_agent.git
cd proj_multi_agent
uv sync
uv run amux --version
```

仓库开发时用 `uv run amux`，例如：

```bash
uv run amux member add claude
uv run roster up claude
uv run amux
```

常用检查命令：

```bash
uv run ruff check .
uv run pytest -q
uv run python -m qa.smoke
```

仓库根目录的 `./start.sh`、`./msg` 和 `hub.py` 是早期总线接口，目前仍为兼容入口。新用户优先
使用 `amux` 命令。

## 项目结构

| 路径 | 内容 |
|---|---|
| `src/console/` | Textual TUI 和命令入口 |
| `src/bus/` | 消息队列、投递、ask/reply 与审计 |
| `src/tmuxctl/` | tmux 会话、画面和进程控制 |
| `src/workspace/` | 多工作区登记与隔离 |
| `src/roster/` | 成员名册和生命周期 |
| `src/team/` | 团队档案与工作区绑定 |
| `tests/` | 自动化测试 |
| `docs/` | 产品、架构、Goal 和质量文档 |

参与开发前请先阅读 [AGENTS.md](AGENTS.md) 和 [文档索引](docs/README.md)。架构边界见
[架构决策](docs/architecture/architecture.md)，打包与发布流程见
[发行文档](docs/releasing.md)。

## 安全说明

- amux 不替你接受 AI CLI 的权限弹窗；权限由各 CLI 自己的正规配置控制。
- `git push`、删除文件、安装软件和访问项目外路径等操作仍应由人明确授权。
- 消息总线会清洗控制字符，并对重复消息、发送频率和积压做传输层限制。
- 手机网关默认无可用用户，必须显式配置白名单后才会接收请求。

问题和建议欢迎提交到 [GitHub Issues](https://github.com/jzhang-0/proj_multi_agent/issues)。

Copyright © 2026 jzhang-0. Licensed under the [MIT License](LICENSE).
