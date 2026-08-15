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
- [ ] **TMX-003** — 输出流订阅:基于 control mode(`tmux -C` 常驻子进程)订阅指定窗格的 `%output` 事件,提供 async 迭代器接口;control mode 不可用时回退 `pipe-pane` 到 FIFO。
  - 前置:TMX-001。
- [x] **TMX-004** — 画面快照 API:`capture-pane -p -e`(带颜色)与去色两种模式,支持历史滚动区(`-S`);对同一窗格的高频请求做节流合并(≥ 10Hz 请求合并为一次)。
  - 前置:TMX-001。
  - 验证:`uv run pytest tests/test_tmuxctl_snapshot.py tests/test_tmuxctl.py -q && uv run ruff check .`
  - 证据:`src/tmuxctl/snapshot.py` 的异步 `PaneSnapshotter` 在线程中调用类型化 `capture_pane`,同参数并发请求共享任务且 100ms 内复用缓存,颜色/历史变体隔离;`tests/test_tmuxctl_snapshot.py` 覆盖转参、10Hz、并发合并、变体隔离及隔离真实 tmux 的 ANSI/去色/`-S` 滚动区,连同 TMX-001 回归共 17 passed;README 已同步 API。
- [ ] **TMX-005** — 进程控制分级:取窗格 `pane_pid` 及子进程树;`interrupt`(send C-c/Escape)、`terminate`(SIGTERM 到 CLI 进程)、`kill`(SIGKILL / kill-session)三级 API,各自幂等。
  - 处理登记:codex,2026-08-16 06:31 +0800,`tmx-005-codex`。
  - 前置:TMX-001。
- [ ] **TMX-006** — 崩溃检测与重启:会话消失或窗格进程退出能在 2 秒内被感知(hook `pane-died` 或轮询兜底);`respawn` API 原地重启成员命令。
  - 前置:TMX-001、TMX-005。
- [ ] **TMX-007** — 活性推断:基于输出流活动量把成员归为 `working`(持续输出)/`idle`(静默且进程在)/`stuck`(被标记工作中但超时静默,阈值可配)/`dead`;只看"有无输出",不解析语义。
  - 前置:TMX-003、TMX-006。
