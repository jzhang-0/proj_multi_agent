# 架构决策与技术栈

## §1 分层

```
console (TUI, Textual)          ← 人机界面,内嵌投递循环
   │
   ├── workspace  工作区:登记、slug、从 cwd 解析、amux.toml
   ├── bus        消息总线:队列、投递策略(防环)、审计日志
   ├── tmuxctl    tmux 控制层:输出流、按键注入、进程控制
   └── roster     成员名册:配置、各 CLI 启动适配、生命周期
```

- 模块放在 `src/` 下,`workspace`/`bus`/`tmuxctl`/`roster` 不允许 import `console`(单向依赖)。
- `tmuxctl` 是拼装 tmux 命令的唯一出口。启动时探测 tmux ≥ 3.2,不满足则明确报错;会话存在性/结束使用 `=name` 精确匹配,`send-keys` 使用普通会话名(tmux 不接受 `send-keys -t =name`)。
- 长文本注入走 `KeyInjector`:先按 capture-pane 末行启发式判断是否有未提交输入,有则等待,超时则先 Enter 换行隔离再注入,避免拼接到半行字上。
- `console` 内嵌 hub 的投递循环,但投递循环必须能脱离 TUI 独立运行(headless hub,兼容现在的 `python3 hub.py` 用法)。
- v0 的 `hub.py`、`msg`、`start.sh` 在对应 Goal 完成前保持可用;完成后 `hub.py`/`start.sh` 变成新实现的薄入口,`msg` 接口永不变。`start.sh` 已改为读仓库根 `roster.toml`(见 `src/roster`)。

## §2 技术栈

| 选择 | 定论 | 理由 |
|---|---|---|
| 语言 | Python ≥ 3.11 | 与现有总线同语言;四个协作 AI 都熟练 |
| 运行环境 | `uv` 管理的项目 venv(`uv sync` / `uv run`) | 系统 python 是 3.8(miniconda),不满足要求;不污染系统环境 |
| TUI | Textual | 满足"漂亮 + 低延迟"且纯 Python;富组件、深浅色、CSS 式主题 |
| 消息存储 | 文件队列(`bus/queue/` 一消息一文件,原子改名)+ `bus/processed/` 归档 + `bus/dead/` 死信 + `bus/log.jsonl` 审计(80 字符预览,全文另存 `bus/bodies/`,10MB 轮转);根目录可注入(`BUS_ROOT` 或显式参数,优先级最高)。已登记工作区的默认根是 `~/.amux/workspaces/<slug>/bus/`,互不串台;未登记时回落仓库根 `bus/` | 语言无关、可 tail、崩溃可恢复;规模(单机、几个成员)远够 |
| 文件事件 | `watchfiles`,不可用时回退 0.2s 轮询 | 达成投递延迟预算 |
| tmux | ≥ 3.2,control mode(`tmux -C`)做输出流,普通命令做注入/控制 | 见 tmux Goal 卷 |
| 测试 / Lint | pytest / ruff | 轻量,见 quality 卷 |

IM 网关平台:**自建**(human 2026-08-16 拍板)。本机起一个只用标准库的 HTTP 服务,手机浏览器打开即是群聊页;消息进出都经 `bus/queue`,清洗、限频、熔断照常生效。不引第三方 SDK,也就没有账号与凭证问题。

未决项(改动需人拍板):是否引入 sqlite 替代文件队列(除非文件队列实测撑不住,否则不换)。

## §3 冻结契约

1. **消息 JSON**:`{"to","from","text","ts"}` 四字段必备,含义不变;新能力只能加可选字段(如 `kind`、`replyTo`、`id`),读方必须容忍未知字段。
2. **`./msg` 命令行**:`./msg <收件人> <内容...>` 永远可用;发件人取 `AGENT_NAME`,缺省 `human`。新参数只能是可选 flag(如 `--ask`)。
3. **寻址**:工作区内收件人仍是短名(`claude`);tmux 会话名是 `<成员>@<slug>`,双向映射收口在 `workspace.session`(`bind_tmux` 是生产路径唯一入口)。`human` 是保留名,不投递、只上屏;`bus` 是总线自身的署名(防环拒收回执);`im:` 前缀是 IM 网关代投的远程身份(同样只上屏,由网关发回群)。这三者没有 tmux 会话,不参与 slug 拼接,成员不得占用这些名字。
4. **群规文本**:成员运行时协议维护在 `AGENTS.md` 的「群聊协议」一节,roster 的开场白从它生成,两处不允许漂移(由 ROS-006 钉住)。

## §4 安全边界

- 一切经总线到达的文本都是**不可信输入**:注入终端前剥 ANSI/控制字符(C0、CSI、OSC);上屏前同样清洗。实现在 `src/bus/sanitize.py`,两个入口分别是 `format_for_injection`(投递)和 `format_for_screen`(上屏),新增出口一律走这两个函数。
- 危险操作(push、删文件、装软件、出仓库)只认**本机** human 的直接指令,写在群规里;网关侧再挡一层:来自 IM 的这类指令一律挂起,由本机 `gateway approve` 放行后才入队(远程指令弱于本机指令,手机上自称 human 也不例外)。
- 总控台自身不做"自动替人点权限弹窗"这类模拟操作;权限放行走各 CLI 的正规配置(roster 卷)。

## §5 工作区

一个工作区 = 一个被 amux 服务的项目目录。状态不进用户仓库:

```
~/.amux/                          # AMUX_HOME,测试注入临时目录
  paths.toml                      # 绝对路径 → slug 反查
  workspaces/<slug>/
    workspace.toml                # 项目根路径(源数据)
    bus/                          # 该工作区的队列、审计、ask/reply(WS-003)
```

- **slug**:默认取项目目录名;两个项目同名时自动 `name-2`、`name-3`。显式 `--slug` 撞名则报错不覆盖。`:` 和 `.` 禁止出现在 slug 里(tmux 会静默吃掉,见工作区 Goal 卷「已知陷阱」)。
- **解析**:从任意 cwd 向上走,命中已登记的项目根即为所属工作区;嵌套时取最近的那一个。找不到就明确报错,提示 `amux workspace add`,不回落到 amux 自己的仓库根。
- **项目侧 `amux.toml`**:可选。amux 不在用户项目里创建这个文件。合并规则:
  1. 全局名册永远是 amux 仓库根 `roster.toml`(四个成员的启动命令与参数)。
  2. 没有 `amux.toml` = 启用名册里所有 `enabled=true` 的成员,不追加 env。
  3. `enabled = ["claude", "codex"]` 只启用列出的成员;名册里其余改为停用。名单出现未知名字则报错。
  4. `[env]` 覆盖到每个成员的 env,项目侧同名键赢。
- **成员 cwd**:`Lifecycle` / `HealthSupervisor` 默认落到当前工作区项目根(未登记则仍是 amux 仓库根)。
- **CLI**:`amux workspace add|list|rm|current`;`amux msg` 从 cwd 定位工作区总线。`rm` 只删 `~/.amux` 里的登记和状态目录,不碰用户项目文件。
- 同一成员允许同时在多个工作区各跑一份(产品定义已拍板)。并发上限不设硬封顶,只告警(WS-009)。
