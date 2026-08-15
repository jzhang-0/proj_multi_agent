# 消息总线(BUS)

现状:v0 的 `hub.py`(轮询投递)+ `msg`(入队)已跑通。本卷把它演化成 `src/bus/` 模块,同时保持 §3 冻结契约。防环策略的参考实现:`reference/pi-extensions/src/talk/policy.ts`(结论:环必须在传输层终止,不依赖模型自觉)。

- [x] **BUS-001** — `src/bus/` 模块化:消息 schema v1(`to/from/text/ts` 必备 + 可选 `id/kind/replyTo`)定义与校验,畸形消息进死信目录不炸投递循环;bus 根目录路径可注入(默认仓库根 `bus/`,测试用临时目录)。
  - 前置:无。
  - 验证:`uv run ruff check . && uv run pytest tests/test_bus_core.py -q`
  - 证据:`src/bus/{message,paths,queue,hub}.py`;`tests/test_bus_core.py` 16 passed(schema 必备/可选/未知字段容忍、8 类畸形载荷、路径注入三级优先级、死信 + 投递异常不中断循环、human 只上屏);`.gitignore` 的 `bus/` 收窄为 `/bus/`,否则 `src/bus/` 会被误忽略。
- [ ] **BUS-002** — 传输层去重:同发件人→同收件人的相同内容 10 秒窗口内直接丢弃,并回执发件人一条说明(防两个 AI 复读死循环)。
  - 处理登记:claude,2026-08-16 06:20 +0800,`bus-002-claude`。
  - 前置:BUS-001。
- [ ] **BUS-003** — 限频:每个 AI 发件人 30 秒窗口最多 8 条,超出拒收并回执;`human` 发件人不受限。
  - 处理登记:codex,2026-08-16 06:09 +0800,`bus-003-codex`。
  - 前置:BUS-001。
- [ ] **BUS-004** — 积压熔断:收件人未投递队列 ≥ 50 时拒收新消息并回执发件人「对方积压中」;单条消息 ≤ 32KB,超长拒收并提示「发摘要和路径,不要发内容」。
  - 前置:BUS-001。
- [ ] **BUS-005** — 投递前清洗:剥离 C0 控制字符(保留换行转空格)、CSI、OSC 转义序列;清洗在投递和上屏两处入口各做一次,附带恶意样本测试(伪造终端标题、光标移动、清屏序列)。
  - 前置:BUS-001。
- [ ] **BUS-006** — 低延迟投递:用 `watchfiles` 监听队列目录(不可用时自动回退 0.2s 轮询),入队到 send-keys 完成 P95 < 200ms;提供 `uv run python -m bus.bench` 输出实测延迟分布。
  - 前置:BUS-001。
- [ ] **BUS-007** — ask/reply 语义:`./msg --ask <收件人> <问题>` 阻塞等待回复(默认 10 分钟超时),投递给收件人的消息里带 ask id 和回复指引;`./msg --reply <id> <答复>` 关联回去。普通用法完全不受影响。
  - 前置:BUS-001。
- [ ] **BUS-008** — 审计日志统一 schema:每条消息记 `deposit/deliver/deliver-failed/rejected` 事件到 `bus/log.jsonl`(拒收含原因),正文只存 80 字符预览 + 全文另存;日志按 10MB 轮转。
  - 前置:BUS-001。
- [ ] **BUS-009** — 契约回归测试:v0 的 `./msg a b c` 用法、四字段 JSON、`human` 保留名语义,各写成契约测试钉死;`hub.py` 与 `start.sh` 成为新实现的薄入口且原用法不变。
  - 前置:BUS-001、BUS-006。
