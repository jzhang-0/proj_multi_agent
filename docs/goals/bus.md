# 消息总线(BUS)

现状:v0 的 `hub.py`(轮询投递)+ `msg`(入队)已跑通。本卷把它演化成 `src/bus/` 模块,同时保持 §3 冻结契约。防环策略的参考实现:`reference/pi-extensions/src/talk/policy.ts`(结论:环必须在传输层终止,不依赖模型自觉)。

- [x] **BUS-001** — `src/bus/` 模块化:消息 schema v1(`to/from/text/ts` 必备 + 可选 `id/kind/replyTo`)定义与校验,畸形消息进死信目录不炸投递循环;bus 根目录路径可注入(默认仓库根 `bus/`,测试用临时目录)。
  - 前置:无。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_core.py -q`
  - 证据:`src/bus/{message,paths,queue,hub}.py`;`tests/test_bus_core.py` 16 passed(schema 必备/可选/未知字段容忍、8 类畸形载荷、路径注入三级优先级、死信 + 投递异常不中断循环、human 只上屏);`.gitignore` 的 `bus/` 收窄为 `/bus/`,否则 `src/bus/` 会被误忽略。
- [x] **BUS-002** — 传输层去重:同发件人→同收件人的相同内容 10 秒窗口内直接丢弃,并回执发件人一条说明(防两个 AI 复读死循环)。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_policy.py -q`
  - 证据:`src/bus/policy.py`(`OutboundPolicy` 按 发件人+收件人+正文 做 10 秒窗口去重,`receipt_for` 生成 `bus` 署名回执)、`src/bus/hub.py` 新增 `rejected` 处理分支;`tests/test_bus_policy.py` + `tests/test_bus_core.py` 23 passed(假时钟窗口内/窗口外、换收件人/发件人/正文三种不去重、回执不再生回执、hub 端到端丢弃 + 回执入队并投出);群规与架构 §3 已补 `bus` 保留名与防复读说明。
- [x] **BUS-003** — 限频:每个 AI 发件人 30 秒窗口最多 8 条,超出拒收并回执;`human` 发件人不受限。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_rate_limit.py tests/test_bus_policy.py tests/test_bus_core.py -q`
  - 证据:`src/bus/policy.py` 按发件人维护 30 秒滑动窗口并豁免 `human`/`bus`,`src/bus/hub.py` 在投递及 human 上屏前统一判定并复用拒收回执;`tests/test_bus_rate_limit.py` 覆盖第 9 条拒收、每发件人隔离、窗口恢复、human 豁免、回执和 Hub 路径,与 BUS-001/002 回归共 31 passed;`AGENTS.md` 已同步限频群规。
- [x] **BUS-004** — 积压熔断:收件人未投递队列 ≥ 50 时拒收新消息并回执发件人「对方积压中」;单条消息 ≤ 32KB,超长拒收并提示「发摘要和路径,不要发内容」。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_backpressure.py tests/test_bus_rate_limit.py tests/test_bus_policy.py tests/test_bus_core.py -q`
  - 证据:`src/bus/policy.py` 在去重/限频前检查每收件人 50 条积压阈值与 32KB UTF-8 正文字节上限,`src/bus/hub.py` 按本轮 FIFO 前序数拒绝第 51 条并复用 `bus` 回执;`tests/test_bus_backpressure.py` 覆盖边界、第 51 条、收件人隔离、UTF-8 与可操作超长提示,同 BUS-001～003 回归共 37 passed;`AGENTS.md` 已同步熔断群规。
- [x] **BUS-005** — 投递前清洗:剥离 C0 控制字符(保留换行转空格)、CSI、OSC 转义序列;清洗在投递和上屏两处入口各做一次,附带恶意样本测试(伪造终端标题、光标移动、清屏序列)。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_sanitize.py -q`
  - 证据:`src/bus/sanitize.py`(`sanitize` 按 OSC → CSI → 其余 ESC → C1 → C0 的顺序剥,`\n\r\t\v\f` 转空格,其余 C0/DEL 删除;两个入口 `format_for_injection` 投递、`format_for_screen` 上屏)、`src/bus/hub.py` 的 `format_line` 改为走清洗;`tests/test_bus_sanitize.py` 15 passed,恶意样本含伪造终端标题(OSC BEL/ST 两种终止)、清屏 + 光标归位、光标上移覆盖、8-bit CSI/OSC、全屏复位 `ESC c`、`\r` 冲掉前半句、退格响铃 NUL,并钉住中英文与 emoji 不被误伤;全量回归 90 passed;架构 §4 已注明两个入口函数。
- [x] **BUS-006** — 低延迟投递:用 `watchfiles` 监听队列目录(不可用时自动回退 0.2s 轮询),入队到 send-keys 完成 P95 < 200ms;提供 `uv run python -m bus.bench` 输出实测延迟分布。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_watch.py -q && uv run python -m bus.bench`
  - 证据:`src/bus/hub.py`(`Hub.run` 走 watchfiles,step 5ms/debounce 20ms,不可用时 `_run_polling` 回退 0.2s;`hub.mode` 暴露实际模式)、`src/bus/bench.py`;`tests/test_bus_watch.py` 4 passed(watch 模式醒、monkeypatch 掉 watchfiles 后 poll 模式醒、存量队列先清空);2026-08-16 实测 `uv run python -m bus.bench`:30 条样本 min 34.2 / P50 50.7 / **P95 94.8** / max 120.9 ms,达标。
  - 顺带:投递里 v0 的盲等 0.3 秒换成"看到目标窗格画面变化就回车"(上限 0.15 秒),否则单条注入本身就超预算;半行拼接的根治仍归 TMX-002。
- [ ] **BUS-007** — ask/reply 语义:`./msg --ask <收件人> <问题>` 阻塞等待回复(默认 10 分钟超时),投递给收件人的消息里带 ask id 和回复指引;`./msg --reply <id> <答复>` 关联回去。普通用法完全不受影响。
  - 处理登记:codex,2026-08-16 06:20 +0800,`bus-007-codex`。
  - 前置:BUS-001。
- [x] **BUS-008** — 审计日志统一 schema:每条消息记 `deposit/deliver/deliver-failed/rejected` 事件到 `bus/log.jsonl`(拒收含原因),正文只存 80 字符预览 + 全文另存;日志按 10MB 轮转。
  - 前置:BUS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_audit.py -q`
  - 证据:`src/bus/audit.py`(`AuditLog.record` 写固定字段 `ts/event/from/to/id/preview` + 可选 `body/kind/replyTo/reason`;`deposit` 在 `queue.deposit` 里记,`deliver`/`deliver-failed`/`rejected` 在 `Hub.drain_once` 里记,拒收带 reason;正文 80 字符预览、全文另存 `bus/bodies/<id>.txt`;`_rotate_if_needed` 到 10MB 改名成 `log-<时间戳>.jsonl`;`entries()` 供 CON-003 回填并跳过坏行);`tests/test_bus_audit.py` 7 passed,全量回归 103 passed。
  - 顺带:多记一个 `malformed` 事件(死信也要可审计),预览过清洗、全文原样留作取证。
- [ ] **BUS-009** — 契约回归测试:v0 的 `./msg a b c` 用法、四字段 JSON、`human` 保留名语义,各写成契约测试钉死;`hub.py` 与 `start.sh` 成为新实现的薄入口且原用法不变。
  - 处理登记:claude,2026-08-16 07:20 +0800,`bus-009-claude`。
  - 前置:BUS-001、BUS-006。
