# 架构决策与技术栈

## §1 分层

```
console (TUI, Textual)          ← 人机界面,内嵌投递循环
   │
   ├── bus     消息总线:队列、投递策略(防环)、审计日志
   ├── tmuxctl tmux 控制层:输出流、按键注入、进程控制
   └── roster  成员名册:配置、各 CLI 启动适配、生命周期
```

- 四个模块放在 `src/` 下,`bus`/`tmuxctl`/`roster` 不允许 import `console`(单向依赖)。
- `console` 内嵌 hub 的投递循环,但投递循环必须能脱离 TUI 独立运行(headless hub,兼容现在的 `python3 hub.py` 用法)。
- v0 的 `hub.py`、`msg`、`start.sh` 在对应 Goal 完成前保持可用;完成后 `hub.py`/`start.sh` 变成新实现的薄入口,`msg` 接口永不变。

## §2 技术栈

| 选择 | 定论 | 理由 |
|---|---|---|
| 语言 | Python ≥ 3.11 | 与现有总线同语言;四个协作 AI 都熟练 |
| 运行环境 | `uv` 管理的项目 venv(`uv sync` / `uv run`) | 系统 python 是 3.8(miniconda),不满足要求;不污染系统环境 |
| TUI | Textual | 满足"漂亮 + 低延迟"且纯 Python;富组件、深浅色、CSS 式主题 |
| 消息存储 | 文件队列(`bus/queue/` 一消息一文件,原子改名)+ `bus/log.jsonl` 审计 | 语言无关、可 tail、崩溃可恢复;规模(单机、几个成员)远够 |
| 文件事件 | `watchfiles`,不可用时回退 0.2s 轮询 | 达成投递延迟预算 |
| tmux | ≥ 3.2,control mode(`tmux -C`)做输出流,普通命令做注入/控制 | 见 tmux Goal 卷 |
| 测试 / Lint | pytest / ruff | 轻量,见 quality 卷 |

未决项(改动需人拍板):IM 网关的平台选型(gateway 卷);是否引入 sqlite 替代文件队列(除非文件队列实测撑不住,否则不换)。

## §3 冻结契约

1. **消息 JSON**:`{"to","from","text","ts"}` 四字段必备,含义不变;新能力只能加可选字段(如 `kind`、`replyTo`、`id`),读方必须容忍未知字段。
2. **`./msg` 命令行**:`./msg <收件人> <内容...>` 永远可用;发件人取 `AGENT_NAME`,缺省 `human`。新参数只能是可选 flag(如 `--ask`)。
3. **寻址**:收件人名字 = tmux 会话名;`human` 是保留名,不投递、只上屏。
4. **群规文本**:成员运行时协议维护在 `AGENTS.md` 的「群聊协议」一节,roster 的开场白从它生成,两处不允许漂移(由 ROS-006 钉住)。

## §4 安全边界

- 一切经总线到达的文本都是**不可信输入**:注入终端前剥 ANSI/控制字符(C0、CSI、OSC);上屏前同样清洗。
- 危险操作(push、删文件、装软件、出仓库)只认 human 直接指令,写在群规里,且网关(远期)侧还要再挡一层。
- 总控台自身不做"自动替人点权限弹窗"这类模拟操作;权限放行走各 CLI 的正规配置(roster 卷)。
