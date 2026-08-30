# tmux 控制层(TMX)

`src/tmuxctl/`:总控台与成员终端之间的全部 tmux 交互都收口在这一个模块,其他模块不允许直接拼 tmux 命令。已验证的能力边界见仓库探针记录:send-keys 全键盘、pipe-pane 实时流、pane_pid 可取可杀、capture-pane 可截画面。

- [x] **TMX-001** — 版本探测与命令封装:启动时探测 tmux ≥ 3.2,不满足给出明确报错;`has-session/new-session/kill-session/send-keys/capture-pane/list-panes` 的类型化封装,统一超时与错误处理。
  - 前置:无。
  - 验证:`uv run ruff check . && uv run pytest tests/test_tmuxctl.py -q`
  - 证据:`src/tmuxctl/{__init__,errors,version,client}.py`;`tests/test_tmuxctl.py` 12 passed(版本过低/缺失/超时、六个命令 argv 与 `=name` 精确匹配、send-keys 不用 `=name`、隔离 socket 真实会话生命周期)。
- [x] **TMX-002** — 按键注入 API:文本(`-l` 字面模式)+ `Enter`/`Escape`/`C-c`;注入长文本前检测目标输入框是否有未提交内容(capture-pane 末行启发式),有则等待或换行隔离,解决 v0 的"半行字拼接污染"问题。
  - 前置:TMX-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_tmuxctl_inject.py tests/test_tmuxctl.py -q`
  - 证据:`src/tmuxctl/inject.py`(`KeyInjector.text/enter/escape/interrupt`);`tests/test_tmuxctl_inject.py` 覆盖末行启发式(含 capture-pane 空行补齐)、空闲直注、等待后注入、超时 Enter 隔离、控制键跳过检测;隔离 socket 下 bash 半行 `echo HELLO` 再注入 `echo WORLD` 不出现 `HELLOecho`。
  - 补记(2026-08-16,GATE-003 实测暴露):末行启发式对**成员 CLI** 没有判别力——cursor / codex / agy 底部都常驻状态栏,末行永远"不像提示符",于是每次投递都白等一轮 capture-pane 再敲一记隔离 Enter,成员正忙时那一记是乱按。当时投递路径改走 `KeyInjector.deliver()`(文本 + Enter 同一次 tmux 调用)+ `ensure_submitted()`(事后确认,只在光标停在自己那行字上时才补 Enter)；该临时方案后来由 TMX-008 的分离注入、Claude composer 探针和可靠归档契约取代。`text()` 保留原语义给 shell 窗格用。
- [x] **TMX-003** — 输出流订阅:基于 control mode(`tmux -C` 常驻子进程)订阅指定窗格的 `%output` 事件,提供 async 迭代器接口;control mode 不可用时回退 `pipe-pane` 到 FIFO。
  - 前置:TMX-001。
  - 验证:`uv run pytest tests/test_tmuxctl_output.py tests/test_tmuxctl_snapshot.py tests/test_tmuxctl_process.py tests/test_tmuxctl_lifecycle.py tests/test_tmuxctl.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/output.py` 的 `PaneOutputStream` 以常驻 `tmux -C attach-session` 解析并解码目标 pane 的 `%output`,暴露 async iterator/context manager;control 握手失败自动切 `pipe-pane` + 0600 FIFO,增量 UTF-8 读取并完整清理;`tests/test_tmuxctl_output.py` 在隔离真实 tmux 分别跑通 control/FIFO 且关闭不杀会话,同 TMX-001/004/005/006 回归共 33 passed;README 已同步。
- [x] **TMX-004** — 画面快照 API:`capture-pane -p -e`(带颜色)与去色两种模式,支持历史滚动区(`-S`);对同一窗格的高频请求做节流合并(≥ 10Hz 请求合并为一次)。
  - 前置:TMX-001。
  - 验证:`uv run pytest tests/test_tmuxctl_snapshot.py tests/test_tmuxctl.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/snapshot.py` 的异步 `PaneSnapshotter` 在线程中调用类型化 `capture_pane`,同参数并发请求共享任务且 100ms 内复用缓存,颜色/历史变体隔离;`tests/test_tmuxctl_snapshot.py` 覆盖转参、10Hz、并发合并、变体隔离及隔离真实 tmux 的 ANSI/去色/`-S` 滚动区,连同 TMX-001 回归共 17 passed;README 已同步 API。
- [x] **TMX-005** — 进程控制分级:取窗格 `pane_pid` 及子进程树;`interrupt`(send C-c/Escape)、`terminate`(SIGTERM 到 CLI 进程)、`kill`(SIGKILL / kill-session)三级 API,各自幂等。
  - 前置:TMX-001。
  - 验证:`uv run pytest tests/test_tmuxctl_process.py tests/test_tmuxctl.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/process.py` 的 `ProcessController` 暴露 pane PID/递归 `ProcessTree`,软打断发 Escape+C-c,terminate/kill 对 CLI 子进程发 SIGTERM/SIGKILL,kill-session 与目标/PID 消失均幂等;`tests/test_tmuxctl_process.py` 覆盖树隔离、三级信号、重复调用、竞态及隔离真实 tmux,同 TMX-001 回归共 20 passed;README 已同步 API。
- [x] **TMX-006** — 崩溃检测与重启:会话消失或窗格进程退出能在 2 秒内被感知(hook `pane-died` 或轮询兜底);`respawn` API 原地重启成员命令。
  - 前置:TMX-001、TMX-005。
  - 验证:`uv run pytest tests/test_tmuxctl_lifecycle.py tests/test_tmuxctl_process.py tests/test_tmuxctl.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/lifecycle.py` 的 `CrashMonitor` 安装唯一 `pane-died[index]` hook 并每 250ms 轮询 pane_dead/会话存在性,hook 失败自动降级;`Tmux.respawn_pane`/`CrashMonitor.respawn` 原 pane 重启;`tests/test_tmuxctl_lifecycle.py` 覆盖 hook、poll、会话消失、超时、委托参数及隔离真实 tmux 的 SIGTERM→2 秒内检测→respawn,同前置回归共 25 passed;README 已同步。
- [x] **TMX-007** — 活性推断:基于输出流活动量把成员归为 `working`(持续输出)/`idle`(静默且进程在)/`stuck`(被标记工作中但超时静默,阈值可配)/`dead`;只看"有无输出",不解析语义。
  - 前置:TMX-003、TMX-006。
  - 验证:`uv run pytest tests/test_tmuxctl_activity.py tests/test_tmuxctl_output.py tests/test_tmuxctl_lifecycle.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/activity.py` 的 `ActivityTracker` 仅记录时间戳与字节数,以近期非空输出判定 `working`、存活静默判定 `idle`、显式工作标记超过可配阈值判定 `stuck`、进程消失判定 `dead`;`ActivityMonitor` 可直接消费 TMX-003 异步输出流且结束时转为 `dead`;`tests/test_tmuxctl_activity.py` 覆盖假时钟、阈值配置、语义无关性与隔离真实 tmux 输出流,连同 TMX-003/006 回归共 17 passed;README 已同步。

- [x] **TMX-008** — 成员消息可靠提交：修复 Claude/Codex 等 TUI 把总线文字留在输入框、Enter 被粘贴突发吞掉的问题。文本与提交键必须分开发送并保留 CLI 处理间隔；同一收件人的消息必须串行，上一条未确认提交时不得继续追加；提交确认不能依赖“终端光标位于输入文字行”的旧假设。未确认的消息不得只记 `deliver` 后归档，必须留下可重试状态与明确审计；补多条积压、折行输入、新版 Claude 底部光标、失败重试及真实 Claude pane 验证。
  - 前置：TMX-002、BUS-008。
  - 验证：`uv run ruff check src tests && uv run pytest tests/test_tmuxctl_inject.py tests/test_bus_*.py tests/test_console_app.py tests/test_console_timeline.py tests/test_console_work.py tests/test_qa_smoke.py tests/test_qa_perf.py -q && uv run python -m qa.smoke`；`uv run python -m bus.bench --count 50 --interval 0.2`。
  - 证据：`KeyInjector.deliver()` 把字面文本与 Enter 拆成两次 tmux 调用并保留 10ms 间隔；真实 Claude Code 2.1.251 Sonnet pane 的 10ms 与 0ms 分离调用均一次提交，取保守的 10ms 默认值。`submission_still_pending()` 优先识别 Claude 底部双横线 composer 的完整折行正文，不再依赖驻留在状态区的 `cursor_y`，且不会把具有相同回复提示尾部的另一条消息误认成当前消息。Hub 逐条同步确认，同收件人未确认时保留 queue、阻止后续追加、只记一次失败审计且不重复注入，其他收件人继续投递；确认后才归档并记 `deliver`。145 项相关回归通过，`qa.smoke` 为 40.7ms；50 条实测 P95 67.0ms（预算 200ms）。真实 `opus@proj_sv_1` 现场在 `cursor_y=41` 时识别出 Fable 消息仍在 composer，补一次 Enter 后返回 `SubmitOutcome(submitted=True, retries=1)` 并立即进入处理态。
