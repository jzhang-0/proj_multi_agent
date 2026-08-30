# amux

**在一台机器上，把多个 AI CLI 组织成一个可观察、可沟通、可接管的协作团队。**

amux 是一个基于 tmux 的终端总控台。Claude Code、Codex、Cursor Agent 等 CLI
仍在各自独立的真实终端里运行；amux 把它们集中到一个 TUI 中，负责成员状态、终端画面、
消息投递和审计记录。

当前版本为 **v0.1.0（Alpha）**，采用 [MIT License](LICENSE)。它适合愿意尝试本地多
Agent 工作流的开发者；当前已包含任务账本、Leader 验收和接管责任流，范围与完成证据见
[产品定义](docs/product/product.md) 和 [Goal 清单](docs/goals/README.md)。

## 它解决什么问题

同时使用多个 AI CLI 时，常见做法是开很多终端，再靠人记住谁在做什么、手动复制消息、来回
切窗口。amux 把这些操作收进一个界面：

- 每个成员拥有独立 tmux 会话，并在当前项目目录中工作；
- 一个界面查看任务看板、验收证据、工作对话记录、成员状态和成员终端画面；
- 可以向成员发消息、等待关联回复，或直接操作成员终端；
- 消息、投递结果和控制动作写入审计日志；
- 派工、进展、证据、评审、退回、接管、验收和 human 汇报写入不可覆盖任务账本；
- 同一台机器可以登记多个项目，各自拥有独立成员和消息总线。

amux 不替代 Claude Code、Codex 等工具，也不会模拟点击权限弹窗。默认适配会按团队策略让
Claude 使用 auto mode、让 Codex 使用完整文件与命令权限；账号、组织策略和操作系统保护仍然生效。

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

- 绑定团队时默认进入任务看板，`F3` 随时返回任务，`/task T-001` 直达详情；
- `Esc` 或 `F2` 返回工作对话记录；
- 工作对话记录顶部按 human、AI、任务、控制分类并显示数量；点击分类，或 Tab 聚焦后用 ←/→ 筛选；
- 在成员画面中直接输入，可以操作该成员的终端；
- PgUp/PgDn、`Ctrl+↑/Ctrl+↓` 或滚轮可以回看成员的 tmux 历史；
- 直连输入为空时，↑/↓ 可操作成员终端菜单，Delete/Backspace、Forward Delete、Enter 和 Shift+Tab 也会作为独立按键透传；
- 点击成员画面中 Claude/Codex 自己的底部输入区，可直接实时键入并看到即时回显；`Esc` 退出实时直连，F8 仍用于完整接管；
- 输入 `@` 会显示全体成员候选，Tab/方向键选择、回车落定；以 `@成员名` 开头的内容会走消息总线；
- `Ctrl+V` 读取系统剪贴板图片，可单独发送或与文字一起发送；输入为空时按 Backspace/Delete 可逐张撤销误贴图片，图片保存在当前工作区状态目录；
- `F1` 查看完整快捷键；
- `q` 或 `Ctrl-C` 退出总控台，不会自动终止成员会话。

也可以从另一个终端直接派发消息：

```bash
amux msg claude "检查当前项目并告诉我最值得先修的问题"
amux msg --ask claude "测试是否已经通过？"
amux msg --task T-001 claude "这条讨论关联到任务 T-001"
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

### 启动 Web 控制台

桌面 Web 控制台与 TUI 并列运行，当前只提供只读观察和导航；它读取同一工作区的任务、成员、工作对话记录和健康状态，不建立第二套状态库：

```bash
uv run amux web --port 8787
```

服务只监听 `127.0.0.1`（默认端口 `8787`），启动后把终端打印的带一次性 token 地址复制到本机浏览器。首次访问会把 token 换成进程内的 `HttpOnly`、`SameSite=Strict` 会话 cookie；服务重启后旧地址失效。WebSocket 实时流同样要求受信任的 Host、Origin 和会话 cookie。

Web 前端使用 Preact + TypeScript + esbuild；Node/npm 只在维护者构建和测试阶段需要，运行已构建的 Python 包不依赖 Node、源码路径或 CDN。浏览器输入、附件发送和成员生命周期/控制动作分别由 WEB-006、WEB-008 实现，当前版本不要把只读 Web 页当成这些能力已经完成。

### 使用默认团队

如果本机已经安装了默认团队需要的 Claude、Codex、Cursor `agent` 与 `agy`（Gemini）CLI，可以初始化并激活内置的
`fable-core` 团队档案（Leader fable + sonnet/opus/luna/sol/composer/grok/agy）：

```bash
amux team init
amux team show fable-core
amux team activate fable-core
amux team current
```

团队档案记录 Leader、成员、模型偏好和职责。激活后，amux 会把团队成员投影到当前工作区名册。
需要扩展已保存团队时，可用 `add-member` 追加成员；它只修改团队档案，不启动进程或修改工作区名册，完成后再显式激活：

```bash
amux team add-member fable-core reviewer \
  --model "Review Model" --responsibility "检查实现与证据" \
  --command reviewer-cli
amux team activate fable-core
```

也可用 `--preset claude|codex` 套用默认团队同类启动适配：runner、环境变量和固定权限参数来自预设，而 `--model`/`--effort` 会按本次参数生成；再用 `--command`/`--arg`/`--env` 覆盖。命令会先确认启动器存在于本机。
启动时所有人读取公共提示词，Leader 与普通成员再分别读取自己的角色提示；Leader 还会拿到完整
团队能力名册用于派工。提示正文集中维护在 [`src/amux_runtime/prompts/`](src/amux_runtime/prompts/README.md)，
修改 Markdown 即可，不需要改 Python。
默认团队还会让三个 Claude 成员使用 classic renderer，避免 alternate screen 把 tmux 回滚区清成
`history_size=0`；同时用空 `NO_COLOR` 覆盖 amux 调用环境里可能继承的 `NO_COLOR=1`，让 Claude
把原有 ANSI 颜色交给 tmux 和总控台。三个 Claude 成员统一以 `--permission-mode auto` 启动；
Luna/Sol 统一以 `-s danger-full-access -a never` 启动，不启用 Codex 命令沙箱，也不请求人工批准。
已有旧团队档案可运行 `amux team init --force` 更新后重新激活；不想重启当前 Claude 会话时，也可
在 Claude 内执行一次 `/tui default`（颜色环境和启动权限仍需下次启动或重启才会生效）。

### 使用任务账本

绑定团队后，Leader、执行者和评审者在同一工作区运行以下命令。写操作从 `AGENT_NAME`
识别当前成员；直接在普通终端运行时身份是 `human`，只能查看，不能绕过 Leader 改状态。

```bash
# Leader
amux task create "修复登录页"
amux task assign T-001 sonnet
amux task review T-001 opus
amux task accept T-001 "测试与风险均已检查"
amux task report T-001 "已向 human 汇报结果和证据"

# 执行者
amux task progress T-001 "已定位回归"
amux task evidence T-001 "tests/test_login.py · 4 passed"
amux task submit T-001 "实现完成，请验收"

# 指定评审者
amux task approve T-001 "实现和证据可信"
# 或：amux task return T-001 "缺少边界测试"

# 任何人可回看
amux task list
amux task show T-001
amux task events T-001
```

重新分派用 `amux task reassign`。Leader 接管时必须同时给出原因、范围、已有交付和后续验收：

```bash
amux task takeover T-001 \
  --reason "成员反复卡在同一问题" \
  --scope "补边界实现和回归测试" \
  --delivered "已有分析、初版补丁和失败日志" \
  --verify "Leader 补测后亲自验收"
```

### 设置全局默认值

```bash
amux config init
amux config show
```

这会创建 `~/.amux/config.toml`，其中包含默认成员、自动拉起成员和主题设置。默认配置会尝试
启动全部内置成员，因此请先确认相应 CLI 已安装；也可以直接编辑该文件，只保留自己需要的成员。

## 界面与数据

amux 的主界面由“团队/任务/会话列表 + 主画面 + 底部输入框”组成。绑定团队时任务与证据是
默认主画面，任务详情展示唯一 Leader、执行者、评审者、创建/更新/完成时间、证据、不可覆盖事件流和关联沟通；
选中“工作对话记录”时，主画面按时间合并总线对话与任务账本事件；顶部可筛选 human 往来、
AI 内部协作、任务事件和终端控制，任务行直接显示任务 ID、标题、分派/评审详情与完成时间；
选中成员时，主画面显示该成员的终端镜像。成员画面通过 `capture-pane` 回看 tmux 历史，不会
切进 copy-mode；PgUp/PgDn 按页滚动（MacBook 是 `Fn+↑/Fn+↓`），`Ctrl+↑/Ctrl+↓` 在未被
macOS 系统快捷键占用时可逐行回看，滚轮同样可用。直连输入为空且没有补全候选时，↑/↓ 会
透传到成员终端以操作审批菜单；Delete/Backspace、Forward Delete、Enter 和 Shift+Tab 也会
透传到成员终端。Delete 只能删除成员当前未提交的草稿，已经
提交并显示在对话记录里的消息不能编辑。输入不为空时删除键只编辑本地文字。成员画面处于
当前画面时，点击 Claude 双横线输入框或 Codex 底部 `›` 输入行会进入实时直连；字符、方向键、
删除键和 Enter 按顺序进入该成员终端，`Esc` 退出。输出区和回滚历史不会误激活。F5–F8 分别用于
打断、终止、重启和全屏接管。

每个工作区的运行时数据默认位于：

```text
~/.amux/workspaces/<slug>/
├── workspace.toml   # 项目路径
├── members.toml     # 当前工作区成员
├── team.toml        # 当前绑定的团队
├── attachments/     # Ctrl+V 粘贴并持久化的本机图片
├── bus/             # 消息队列、正文和审计日志
└── work/events.jsonl # 哈希链保护的只追加任务账本
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
./install-amux.sh dev
```

`./install-amux.sh dev` 会在 `~/.local/bin` 生成指向当前 checkout 的 `amux-dev`，不会覆盖
PyPI 安装的 `amux`。它默认与正式版共用 `~/.amux`，因此已保存的团队、工作区绑定和成员名单
都能直接用于开发版；只有需要完全隔离的测试时才显式设置 `AMUX_DEV_HOME`。

仓库开发时可用 `uv run amux`，也可进入任何其他项目直接运行 `amux-dev`：

```bash
uv run amux member add claude
uv run roster up claude
uv run amux

cd /path/to/another-project
amux-dev

AMUX_DEV_HOME=/tmp/amux-sandbox amux-dev  # 可选：使用隔离状态
./install-amux.sh uninstall-dev           # 卸载开发入口
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
| `src/work/` | 任务事件账本、状态投影和 Leader 责任流 |
| `src/bus/` | 消息队列、投递、ask/reply 与审计 |
| `src/tmuxctl/` | tmux 会话、画面和进程控制 |
| `src/workspace/` | 多工作区登记与隔离 |
| `src/roster/` | 成员名册和生命周期 |
| `src/team/` | 团队档案与工作区绑定 |
| `src/amux_runtime/prompts/` | 公共、Leader 与普通成员运行时提示词 |
| `tests/` | 自动化测试 |
| `docs/` | 产品、架构、Goal 和质量文档 |

参与开发前请先阅读 [AGENTS.md](AGENTS.md) 和 [文档索引](docs/README.md)。架构边界见
[架构决策](docs/architecture/architecture.md)，打包与发布流程见
[发行文档](docs/releasing.md)。

## 安全说明

- amux 不模拟接受 AI CLI 的权限弹窗；默认团队通过各 CLI 的正规启动参数启用 Claude auto mode
  和 Codex 完整权限，仍不能绕过账号、组织策略或操作系统保护。
- 内置 Codex 预设不启用命令沙箱或人工审批；请只在可信工作区和可信本机环境中使用。
- `git push`、删除文件、安装软件和访问项目外路径等操作仍应由人明确授权。
- 消息总线会清洗控制字符，并对重复消息、发送频率和积压做传输层限制。
- 手机网关默认无可用用户，必须显式配置白名单后才会接收请求。
- Web 控制台只监听 `127.0.0.1`；HTTP 请求校验 Host，WebSocket 额外校验 Origin，并且 API 只接受启动时 token 换出的进程内 `HttpOnly`、`SameSite=Strict` cookie。token 不落盘，地址换 cookie 后重定向去掉查询参数。
- 浏览器传来的 actor、工作区、路径、tmux target 和任务权限声明都不可信；Web 不开放任意 shell，服务端只允许既定的工作区/成员路由。API 响应和附件下载不得泄露本机绝对路径，终端 ANSI 只按安全的终端数据渲染。
- WEB-008 的成员终止、重启、关闭会话、完整接管等危险动作必须二次确认并写入控制审计；WEB-006/008 未结项前，Web 端仍按只读能力使用。
- Web 依赖随 `amux-team` 默认安装，维护者在发布前需先完成 `npm build`（或 `npm run verify`）再执行 `qa.release`；发布验证不等于开发服务器可以访问公网。

问题和建议欢迎提交到 [GitHub Issues](https://github.com/jzhang-0/proj_multi_agent/issues)。

Copyright © 2026 jzhang-0. Licensed under the [MIT License](LICENSE).
