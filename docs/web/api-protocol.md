# Web API 与实时协议设计稿(WEB-003 / WEB-004)

本文是 WEB-003(后端骨架与一致 snapshot API)和 WEB-004(versioned 实时事件流)的实现依据：资源清单、DTO 字段、`epoch/revision` 语义、WebSocket 消息形态、断线 resync、慢客户端策略与本机认证会话。目标是实现者照本文即可动手，不需要再猜语义。

- 边界与路线依据：[Web 版架构方案比较 §2](architecture-options.md#2-先确定共用边界)、[功能盘点 §2](inventory.md#2-tui-画面与操作总表)
- 上游前置：WEB-001 控制面。**本文的 DTO 字段按当前 `console` / 领域层现状起草，WEB-001 合入后按 §10 逐项校正**，以控制面实际输出为准。
- 本文只写设计，不含实现。

## 1. 分层与硬边界

```text
浏览器 SPA ──HTTP/WS──> web 后端(FastAPI/ASGI) ──> control plane(WEB-001) ──> workspace / team / roster / work / bus / tmuxctl
```

四条不可越界的规则：

1. **后端不得绕过控制面直接碰领域层**。`WorkService`、`AuditLog`、`MemberStatusService` 等一律经控制面调用；后端自己不建第二套状态库、不缓存领域投影之外的业务状态。
2. **只读 API 不得产生任何副作用**。特别是不得触发 `Tmux.fit_window()` / `release_window_size()` / `send_keys` —— TUI 的 `ConsoleApp._start_mirror()` / `_fit_member_window()` 把"看"和"改尺寸"绑在一起，Web 侧必须拆开。尺寸归 WEB-002 的交互租约持有者决定。
3. **WEB-003/004 不引入租约**。snapshot 与事件流是纯观察，多个浏览器 tab 与 TUI 可任意并存。租约只约束 resize / 直连 / 接管(WEB-002、WEB-007)。
4. **actor 恒来自认证会话**，首版固定为 `human`。任何请求体、查询参数、WS 帧里自报的 actor / 路径 / tmux target 一律丢弃，不作为默认值、不作为回落。

## 2. 通用约定

### 2.1 传输与命名

- 前缀 `/api/v1`；只监听 `127.0.0.1`，端口默认 `8787`，被占用则 `--port` 显式指定(不自动漂移，避免用户不知道连到了哪个进程)。
- 请求与响应一律 `application/json; charset=utf-8`，`ensure_ascii=false`。
- JSON 字段名用 `snake_case`。例外：透传总线消息 schema v1 的字段时保持其原名(`replyTo`、`mediaType`)，因为那是[冻结契约](../architecture/architecture.md)，不允许在传输层改写。
- 所有响应带 `Cache-Control: no-store`。`/api/v1/vocabulary` 例外，可按 epoch 缓存(见 §5.2)。

### 2.2 时间

**DTO 中的时间字段只有两种形态**，不出现 `datetime`、不出现已格式化的相对时间字符串：

| 形态 | 类型 | 用途 |
|---|---|---|
| `*_at` | `float`，Unix epoch 秒(UTC) | 一切需要参与计算、排序、相对时间显示的时刻 |
| `*_ts` | `str` | 账本/审计日志里的原始时间字符串，原样透传，仅供显示与取证对照，**前端不得拿它做时间运算** |

理由是现有两个数据源的时间格式不一致，必须由服务端归一：

- `work/events.jsonl` 的 `ts` 是 UTC ISO-8601 带 `Z`(`src/work/ledger.py:226`)；
- `bus/log.jsonl` 的 `ts` 是**本机时区的朴素字符串** `%Y-%m-%d %H:%M:%S`，不带偏移(`src/bus/message.py:17`)。

浏览器无法从后者还原时刻。服务端在投影时按"审计日志 = 服务端本机时区，账本 = UTC"解析成 epoch 秒填入 `*_at`；原串保留在 `*_ts`。

**单调时钟不得出网**：`ActivityTracker` 用 `time.monotonic()`，`ActivitySnapshot.last_output_at` 是进程内单调值(`src/tmuxctl/activity.py:56`，`ActivitySnapshot` 见 `src/tmuxctl/activity.py:31-42`)，跨进程无意义，禁止序列化。成员空闲时长改用同一 dataclass 已有的 `silent_for`(墙钟无关的秒数)加上快照时刻，见 §4.10。

### 2.3 文本安全

- 一切来自成员、消息正文、任务说明、证据引用、事件 `data` 的自由文本，在出 DTO 前过 `bus.sanitize.sanitize()`，与 TUI 同一套规则；DTO 里是**已清洗文本**。
- 需要未清洗原文时(取证)，走独立端点 `GET /api/v1/timeline/{seq}/body`，响应 `text/plain; charset=utf-8` + `Content-Disposition: attachment`，前端**不得**把它渲染进 DOM 或终端组件。
- 绝对路径：`workspace.project_root` 是 Header 副标题的既有内容(inventory §2 第 1 行)，首版在 loopback + token 会话下返回。**附件与证据的文件路径不在此列**，一律只回内容寻址 id，绝不回绝对路径(WEB-006 约束前置在此)。

### 2.4 错误模型

```json
{"error": {"code": "work-unavailable", "message": "任务账本不可用：...", "domain": "work"}}
```

| code | HTTP | 场景 |
|---|---|---|
| `unauthorized` | 401 | 无有效会话 cookie、或 `Origin`/`Host` 校验失败 |
| `invalid-request` | 400 | 参数形状非法 |
| `not-found` | 404 | 任务 ID / 成员名不存在 |
| `workspace-unregistered` | 409 | cwd 不属于任何已登记工作区 |
| `team-unbound` | 409 | 工作区未绑定团队(`WorkService.for_workspace` 抛错) |
| `work-unavailable` | 503 | `WorkError` / `LedgerCorruptionError` / 账本 IO 失败 |
| `epoch-mismatch` | 409 | 请求带的 `epoch` 与当前不符(见 §4) |

硬性要求：**账本损坏不得变成 500**。`LedgerCorruptionError` 必须落到 `work-unavailable`，前端显示对齐 TUI 的 `TaskDetail.show_error()`，界面其余部分(时间线、成员栏)继续可用。

## 3. epoch 与 revision

### 3.1 语义

- **`epoch`**：不透明字符串(16 位 hex)，标识"当前这一代服务端状态"。服务端启动时生成一次；**工作区重绑定(`/workspace` 等价操作)时重新生成**。epoch 变化意味着客户端所有缓存作废，必须全量重取。客户端只比较相等，不比较大小。
- **`revision`**：`uint64`，**每个域一个**独立计数器，同一 epoch 内从 `0` 起严格 `+1` 递增、不跳号。任何一次该域的变更 bump 一次。

epoch 的存在正是为了让 revision 可以是**进程内内存计数器，不需要持久化**：进程重启后 revision 归零，但 epoch 已变，客户端不会把新的低 revision 误判成回退。

### 3.2 域清单

| domain | 含义 | 变更来源 |
|---|---|---|
| `workspace` | 当前绑定工作区 | 工作区切换(同时换 epoch) |
| `team` | 团队档案与 Leader | `teams/*.toml`、`team binding` |
| `roster` | 名册启用成员、`/adopt` 临时成员 | `roster.toml`、`members.toml`、进程内 adopt |
| `work` | 任务账本投影(tasks + events) | `work/events.jsonl` 追加 |
| `timeline` | 工作对话记录流 | `bus/log.jsonl` 追加、`work` 事件追加 |
| `member` | 成员卡片状态聚合 | 0.5s 聚合定时器(不走文件监视) |
| `health` | 故障/恢复边沿 | `ConsoleHealthMonitor` 的 `FaultEvent` |

一次账本追加同时 bump `work` 与 `timeline` —— 任务事件既进任务详情，也进时间线(`TimelineEntry.from_work_event`)。两个计数器各自 `+1`，互不借用。

### 3.3 变更检测与 bump

- 文件类(`work`/`timeline`/`team`/`roster`)用 `watchfiles` 监视对应文件，**debounce 50ms**：一条消息在 50ms 内可能产生 `deposit` + `deliver` 两行，合并成一次 bump。
- `member` 域不监视文件，由控制面的成员状态聚合以 **0.5s** 周期计算；**只有聚合结果与上次不同才 bump**(对齐 TUI `refresh_member_cards` 的 0.5s 节奏，但不做无变化的空推送)。
- `health` 域由 `FaultEvent` 边沿直接 bump。
- 变更检测的**判据**是内容指纹，不是"文件被 touch"：`work` 用 `snapshot.events[-1].digest`(哈希链末端，TUI 已有此法 `ConsoleApp._snapshot_digest`)，`timeline` 用 `(log.jsonl mtime_ns, size)`(TUI 的 `_audit_stamp`)。指纹是检测输入，revision 是传输序号，两者不可混用、不可互相替代。

### 3.4 携带位置

- 每个 snapshot 响应体带 `"epoch"` 与该资源所属域的 `"revision"`。
- 复合响应(`/bootstrap`、`/session`)带 `"epoch"` 与 `"revisions": {domain: rev}` 全表。
- WS 每条 `invalidation` / `delta` 带 `domain` 与 `revision`。

## 4. Snapshot 资源清单

只读；全部要求 `GET`。写操作属 WEB-006 / WEB-008，本文只在 §6.4 定死其鉴权前提。

| 路径 | 域 | 说明 |
|---|---|---|
| `/api/v1/session` | — | 会话身份、epoch、全域 revision、服务端能力 |
| `/api/v1/bootstrap` | 多 | 首屏一次取齐，省往返 |
| `/api/v1/vocabulary` | — | 状态/事件/分类的枚举词表 |
| `/api/v1/workspace` | `workspace` | 顶部 Header / 工作区副标题 |
| `/api/v1/team` | `team` | 团队与 Leader |
| `/api/v1/work` | `work` | 左栏任务摘要 + 任务列表 |
| `/api/v1/work/tasks/{id}` | `work` | 中栏任务详情、证据、事件流、关联工作对话 |
| `/api/v1/timeline` | `timeline` | 工作对话记录时间线(分页、分类筛选) |
| `/api/v1/timeline/{seq}/body` | `timeline` | 单条消息全文(取证，非渲染) |
| `/api/v1/members` | `member` | 左栏成员卡片 |
| `/api/v1/health` | `health` | 健康告警 |

WEB-006 另增加 `POST /api/v1/messages`、`POST /api/v1/attachments` 与
`GET /api/v1/attachments/{id}`，写入与下载契约见 §6.5；它们不是 snapshot 资源。

### 4.1 `GET /api/v1/session`

```json
{
  "actor": "human",
  "write_token": "<当前进程会话 id>",
  "epoch": "9f3c1ab27de40561",
  "epoch_started_at": 1756512000.0,
  "server_time_at": 1756512034.5,
  "revisions": {"workspace": 0, "team": 0, "roster": 2, "work": 7, "timeline": 41, "member": 118, "health": 0},
  "capabilities": {"stream": true, "mirror": false, "compose": true, "control": false}
}
```

`capabilities` 让 SPA 在 WEB-005～008 逐步落地期间能按能力开关界面，而不是靠版本号猜。WEB-003 交付时除 `stream` 外全 `false`。
`write_token` 仅在有效 HttpOnly cookie 会话内返回，SPA 原样放进 §6.3 的
`X-Amux-Session`；它不持久化，进程退出即失效。

### 4.2 `GET /api/v1/bootstrap`

`{"epoch", "revisions", "session", "workspace", "team", "work", "members", "health", "timeline"}`，各子对象与独立端点同构；`timeline` 取最近一页。一次请求拿到首屏全部内容，且**这一份是同一时刻的一致读取**(同一次控制面读取内完成)，避免首屏各端点分别读到不同代的数据。

### 4.3 `GET /api/v1/vocabulary`

枚举的中文标签与无障碍图形是两端共有的领域词表，复制到前端会漂移；颜色是主题，属前端。故：**词表由后端出，颜色不出**。

```json
{
  "epoch": "9f3c1ab27de40561",
  "task_status": [{"value": "in-progress", "label": "进行中", "glyph": "▶"}, "..."],
  "event_kind":  [{"value": "assigned", "label": "派工"}, "..."],
  "member_state":[{"value": "working", "label": "WORK", "glyph": "▶"}, "..."],
  "timeline_category": [{"value": "human", "label": "human往来"}, "..."],
  "timeline_outcome":  [{"value": "delivered", "label": "已投递", "glyph": "✓", "dim": false}, "..."],
  "event_detail_fields": [{"key": "summary", "label": "摘要"}, "..."]
}
```

来源分别是 `STATUS_LABELS` / `TASK_GLYPHS`、`EVENT_LABELS`、`STATUS_GLYPHS`、`CATEGORY_LABELS`、`OUTCOME_MARKS`、`DETAIL_FIELDS`。`dim` 是"这不是正常发言"的语义标志(拒收/失败)，不是颜色。

### 4.4 `GET /api/v1/workspace`

```json
{"epoch": "...", "revision": 0,
 "registered": true, "slug": "proj-multi-agent", "project_root": "/Users/x/proj_multi_agent"}
```

未登记时 `{"registered": false, "slug": null, "project_root": null}`。

**不返回 `subtitle` / `banner`**：`ConsoleApp._workspace_subtitle()` 产出的 `"<slug> · <root>"` 是拼接后的呈现结果，两端排版不同(TUI 是一行副标题，Web 是 Header 区域)，由各前端自行组合。未登记提示文案同理。

### 4.5 `GET /api/v1/team`

```json
{"epoch": "...", "revision": 0,
 "bound": true, "id": "fable-core", "name": "...", "description": "...", "leader": "fable",
 "members": [{"id": "opus", "role": "member", "model": "Opus", "effort": "high",
              "speed": "standard", "responsibility": "复杂问题分析、方案评审与风险检查"}]}
```

**`command` / `args` / `env` 不出网**：那是启动适配(进程命令行与环境变量)，属运行时机密面，Web 只读控制台没有展示需求，暴露它等于把可执行命令与环境变量交给浏览器。未绑定团队时 `{"bound": false}` 且其余为空，不报错(左栏任务摘要在未绑定时本就不显示)。

### 4.6 `GET /api/v1/work`

```json
{"epoch": "...", "revision": 7,
 "summary": {"leader": "fable", "active": 3, "waiting": 1, "blocked": 0, "total": 6,
             "by_status": {"backlog": 1, "in-progress": 2, "...": 0}},
 "tasks": [{"id": "T-003", "title": "...", "status": "in-progress",
            "assignee": "sol", "reviewer": "opus", "parent_id": null, "completed": false,
            "created_at": 1756400000.0, "updated_at": 1756512000.0,
            "created_ts": "2026-08-30T02:13:20Z", "updated_ts": "2026-08-30T05:20:00Z"}],
 "selected_default": "T-003"}
```

- `summary` 的 `active` / `waiting` / `blocked` 分组沿用 `render_task_summary()` 的既定口径：active = `backlog|assigned|in-progress|blocked|changes-requested`；waiting = `submitted|in-review|reviewed|accepted`；blocked = `blocked + changes-requested`。同时给出 `by_status` 全表，前端要换口径不必改后端。
- `selected_default` 对齐 `ConsoleApp._initial_task_id()`：首个未完成任务，否则首个任务，否则 `null`。这是"默认选中哪一项"的领域口径，两端必须一致，故由后端出。
- 列表项**不含** `description` / `evidence` / `event_ids`，避免任务多时首屏膨胀；详情走 §4.7。

### 4.7 `GET /api/v1/work/tasks/{id}`

```json
{"epoch": "...", "revision": 7,
 "task": {"id": "T-003", "title": "...", "description": "...", "leader": "fable",
          "parent_id": null, "status": "submitted", "assignee": "sol", "reviewer": "opus",
          "accepted_by": null, "completed": false, "latest": "...",
          "created_at": 1756400000.0, "updated_at": 1756512000.0,
          "created_ts": "...", "updated_ts": "...",
          "evidence": ["docs/web/api-protocol.md", "..."]},
 "children": [{"id": "T-004", "title": "...", "status": "backlog"}],
 "events": [{"seq": 12, "id": "...", "kind": "evidence", "actor": "opus",
             "at": 1756511000.0, "ts": "2026-08-30T05:03:20Z",
             "details": {"reference": "docs/web/api-protocol.md"}}],
 "communications": [{"timeline_seq": 88, "sender": "fable", "to": "opus",
                     "text": "...", "attachment_count": 0,
                     "at": 1756510000.0, "ts": "2026-08-30 13:26:40"}]}
```

- `events` 覆盖 `WorkSnapshot.events_for(task_id)` 的全部 14 种 `EventKind`(建立/拆分/派工/进展/阻塞/证据/提交/指定评审/评审通过/评审退回/改派/接管/验收/汇报)，顺序即账本顺序，**不做任何过滤或折叠** —— 这是"不可覆盖事件流"，删一条就破坏了它的意义。
- `details` 是 `event.data` 按 `DETAIL_FIELDS` 白名单**结构化取出的 key→值**，不是 `event_details()` 那种 `"摘要:xxx"` 拼接串。标签在 vocabulary 里，由前端组合。白名单之外的 `data` 键不出网(账本 `data` 是自由字典，全量透传会让未来新增的内部字段意外暴露)。
- `digest` / `previous` 哈希链字段**不出网**：Web 不做链校验，校验在账本读取时已完成；暴露它只会诱导前端做半吊子验证。若将来要给"链完整"提示，出一个 `integrity: "ok" | "broken"` 布尔语义，不出原始哈希。
- `communications` 对齐 TUI：审计日志中 `event=deposit` 且 `task` 等于本任务的条目，取最近 20 条。

### 4.8 `GET /api/v1/timeline`

参数：`category`(`all|human|ai|task|control`，默认 `all`)、`limit`(默认 200，上限 1000)、`before_seq`(向历史翻页)。

```json
{"epoch": "...", "revision": 41,
 "entries": [{"seq": 88, "key": "01J...", "at": 1756510000.0, "ts": "2026-08-30 13:26:40",
              "sender": "fable", "to": "opus", "text": "...",
              "outcome": "delivered", "reason": "", "task_id": "T-003",
              "attachment_count": 0, "category": "ai", "has_body": true}],
 "category_counts": {"all": 412, "human": 51, "ai": 300, "task": 47, "control": 14},
 "head_seq": 88, "oldest_seq": 1, "has_more": false}
```

- **`seq`**：服务端在本 epoch 内为每条进入时间线的记录分配的单调整数，从 1 起。**按到达顺序分配，一经赋给某个 `key` 就不再改**——不按 `at` 排序后的位置 enumerate。进程启动时按审计 jsonl 首次出现的 key（文件序）再接账本事件（账本序）重建，不落盘；每个 epoch 内自洽即可。窗口截取不重编号（超过 `HISTORY_LIMIT` 的条目仍持有全量 seq）。`seq` 是分页游标、delta 定位和未读计算的唯一依据，**不是时间顺序**。
- **展示顺序**：前端（含 TUI）一律按 `at`（同秒再用 `seq`/`key` 稳定次序）排序；不得拿 `seq` 当时序。后到、`at` 更早的记录会插在画面中间，但拿到的是当前最大 seq+1。
- **`key`**：去重键，取消息 `id`；没有 `id` 的 v0 四字段消息按 `ts|sender|to|text` 复合，控制事件按 `control:<index>`，与 `history()` 的合并规则一致。前端据此避免同一条消息在"回填 + 实时"两路重复出现。
- `outcome` 取 `delivered|shown|deliver-failed|rejected|malformed|pending`；一条消息的多个审计事件已由服务端合并为最终结局(取最后一个非 `deposit` 的事件)，前端不做合并。
- `category` 由收发端 / 审计事件类型 / WorkEvent 类型判定，**不解析正文**，规则同 `TimelineEntry.resolved_category`。
- `category_counts` 由服务端算：分类计数是筛选器上的常驻显示，前端若只持有一页就算不准。
- **不返回颜色**。发件人固定着色是 `member_color()` 按 crc32 从主题色池取，属呈现层；前端各自实现同一套 `crc32(name) % len(palette)` 规则即可保持"同名同色"，色池是前端主题的一部分。分钟分组同理：前端按 `ts[:16]` 分组，规则写在此处，不占 DTO 字段。

### 4.9 未读数

**不设服务端未读端点**。未读是"这个客户端上次看到哪儿"，是客户端状态：前端持久化 `last_seen_seq`，未读 = `head_seq - last_seen_seq`。`head_seq` 是本 epoch 已分配的最大 seq（最新到达），不是“时间上最晚那条”的 seq。epoch 变化时 seq 空间作废，前端必须把 `last_seen_seq` 重置为新的 `head_seq`(否则会显示成天量未读)——这条要写进 SPA 的 epoch 处理路径。

### 4.10 `GET /api/v1/members`

```json
{"epoch": "...", "revision": 118, "snapshot_at": 1756512034.5,
 "members": [{"name": "opus", "state": "working", "queued": 2,
              "silent_for": 3.5, "alive": true, "source": "roster"}]}
```

- `state` ∈ `idle|working|stuck|dead|failed`，来源 `MemberStatusService.snapshot()`(含 `override_state` 的 `failed`)。
- `queued` 来源 `pending_counts()`，按收件人统计队列中未投递消息。
- **`silent_for` + `snapshot_at` 取代相对时间字符串**。TUI 的 `relative_activity()` 产出的 `"3分前"` 是呈现结果，且会随读取时刻漂移；推送到浏览器后越放越不准。改出 `ActivitySnapshot.silent_for`(墙钟无关的秒数，dataclass 已有该字段)与快照时刻 `snapshot_at`，前端显示时算 `silent_for + (now - snapshot_at)` 并自行格式化 —— 这样即使推送延迟或客户端挂后台，显示也自校正。`last_output_at` 是单调时钟值，禁止出网(§2.2)。
- `source` ∈ `roster|adopted`，区分名册成员与 `/adopt` 收编的进程级临时成员(inventory §2 明示 `/adopt` 是临时状态，前端要能提示)。
- 成员列表为空(名册读不出来)时返回空数组而非报错 —— 对齐 `console.members` 的既定取舍：名册坏了也要能看总线流量。

### 4.11 `GET /api/v1/health`

```json
{"epoch": "...", "revision": 3, "degraded": true,
 "faults": [{"key": "member-session:sol", "kind": "member-session", "target": "sol", "detail": "..."}]}
```

`kind` ∈ `tmux-server|member-session|bus-unwritable`。这是**当前仍活跃的故障集**；故障与恢复的**边沿**通过 WS 的 `health` delta 推送(§5.4)，让前端能弹一次性提示而不是轮询比对。

## 5. 实时协议(WebSocket)

端点：`GET /api/v1/stream`(升级)。一条连接承载全部域。

### 5.1 帧通用形状

```json
{"type": "...", "epoch": "9f3c1ab27de40561", "domain": "work", "revision": 8, "...": "..."}
```

所有服务端帧都带 `epoch`。客户端收到 `epoch` 与本地不符的任何帧时，**立即丢弃全部缓存并走全量 bootstrap**，不必等 `epoch_changed`。

### 5.2 客户端 → 服务端

| type | 载荷 | 说明 |
|---|---|---|
| `subscribe` | `{domains: [...], known: {domain: revision}, epoch}` | 连接后第一帧。`known` 是客户端已有的 revision，服务端据此决定补发 delta 还是要求 resync |
| `unsubscribe` | `{domains: [...]}` | 例如离开任务视图时退订 `work` |
| `pong` | `{}` | 回应服务端心跳 |

不接受任何写操作帧。写走 HTTP(WEB-006/008)，理由：写操作需要审计、幂等键与错误回执，HTTP 的请求/响应配对比 WS 帧更难写错。

### 5.3 服务端 → 客户端

| type | 载荷 | 说明 |
|---|---|---|
| `hello` | `{epoch, epoch_started_at, revisions, limits}` | 升级成功后第一帧，无条件发送 |
| `invalidation` | `{domain, revision}` | 该域变了，客户端重拉对应 snapshot |
| `delta` | `{domain, revision, ops: [...]}` | 该域的增量，客户端可直接应用 |
| `resync` | `{domain, reason}` | 服务端要求该域全量重取。`reason` ∈ `gap|overflow|corrupt|resubscribe` |
| `epoch_changed` | `{epoch}` | 工作区切换等；客户端丢全部缓存重来 |
| `ping` | `{}` | 心跳 |
| `error` | `{code, message}` | 与 §2.4 同一套 code |

### 5.4 invalidation 还是 delta

按域固定，不由服务端临场决定 —— 客户端能预知每个域收到什么，实现才不会两头下注：

| domain | 形态 | 理由 |
|---|---|---|
| `timeline` | **delta** | append-only、单条小、量大且要求实时；全量重拉一页会撕裂滚动位置 |
| `work` | **delta**(仅 events) + **invalidation**(tasks) | 事件流是 append-only 可增量；任务投影是事件重放的结果，增量打补丁易与服务端投影漂移，直接重拉 |
| `member` | **invalidation** | `state`/`queued`/`silent_for` 是聚合值，全表也就几十个成员，重拉一个小 JSON 比维护 diff 语义可靠 |
| `health` | **delta** | 故障/恢复是边沿事件，前端要按"新增/恢复"弹提示，天然是 op |
| `workspace` / `team` / `roster` | **invalidation** | 低频、整体替换 |

`timeline` delta 的 op：

```json
{"type": "delta", "domain": "timeline", "revision": 42,
 "ops": [{"op": "append", "entry": {"seq": 89, "...": "..."}},
         {"op": "update", "seq": 88, "outcome": "delivered", "reason": ""}]}
```

`update` 是必需的：一条消息先 `deposit`(pending) 后 `deliver`(delivered)，同一 `seq` 的结局会变。前端按 `seq` 就地改，不新增行。

`seq` 按到达分配且对 `key` 稳定（§4.8）：后到、`at` 更早的记录走 `append`（新 seq），不得把已有行的 seq 挤走。若重建后已知 `key` 的 seq 变了，服务端发 `resync{reason:gap}`（WEB-004 防御路径），不产出错位的 `update`+`append`。

`work` delta 的 op：`{"op": "append", "event": {...}, "task_id": "T-003"}`；同一帧可同时带 `{"op": "invalidate", "scope": "tasks"}` 表示任务投影需重拉。

`health` delta 的 op：`{"op": "raise" | "clear", "fault": {...}}`。

### 5.5 断线 resync

服务端每域维护一个 **delta 重放环**：`timeline` 512 条、`work` 512 条、`health` 128 条，按 revision 索引。

客户端重连并 `subscribe` 时，服务端逐域判定：

1. `epoch` 不符 → 回 `epoch_changed`，所有域视为全新，客户端走 `/bootstrap`。
2. `known[domain] == 服务端当前 revision` → 无事发生，不发帧。
3. `known[domain] < 当前` 且 `当前 - known[domain] <= 环容量` 且该域是 delta 域 → 按序补发区间内的 `delta` 帧，客户端追平。
4. 其余(缺口超环容量 / invalidation 域 / `known` 里没有该域) → 回 `resync{domain, reason:"gap"}`，客户端重拉该域 snapshot。

**客户端侧的断档判据**：正常流中收到 `revision != 本地 + 1` 即视为断档，立即对该域走 snapshot 重取，不尝试自行推断丢了什么。服务端保证同一 epoch 内每域 revision 连续 `+1`，所以这个判据是可靠的。

### 5.6 有界队列与慢客户端

每连接一个 `asyncio.Queue(maxsize=256)`。铁律：**领域变更检测与投递永远不被慢客户端阻塞**，入队一律非阻塞。

队列满时按顺序降级，不丢静默、不无限堆积：

1. **合并**：把该连接队列中该域所有待发 `delta` 丢弃，压成一条 `resync{domain, reason:"overflow"}` 入队。慢客户端拿到的是"你落后了，重拉"，而不是一堆过期增量。
2. 合并后仍满(多域同时溢出) → 清空队列，只留一条 `resync` 全域(`domain: "*"`)。
3. 再满(客户端完全不读) → 关闭连接，WS close code `1013`。客户端按指数退避重连(1s 起，上限 30s，带抖动)，重连后走 §5.5。

心跳：服务端每 30s 发一帧 `ping`；90s 内未收到任何客户端帧则关闭连接。用应用层心跳而非协议层 ping —— 浏览器 `WebSocket` API 观察不到协议层 ping/pong，前端无法据此判活。

WEB-007 的终端帧**不走这条连接**：完整帧体积大、频率高(≤10 Hz)，与控制面事件混在一个有界队列里会互相饿死。另开专用 WS，背压策略独立(只保留最新帧)。

### 5.7 握手拒绝与关闭码

WS 握手校验(会话 cookie、`Host`、`Origin`，见 §6.3)失败时，**必须先 `accept()` 再 `close(code, reason)`**，不得在 `accept()` 之前 `close()`。

实测(Sonnet，2026-08-30，真实 uvicorn + `websockets` 客户端，非 `TestClient`)：`accept()` 之前 `close(code=4401)` 会退化成握手阶段的 HTTP 403，浏览器 `WebSocket` API 不把失败握手的状态暴露给 JS，前端只看得到 `error` 后跟 `close(1006, "")`；`accept()` 之后 `close(code=4401, reason="unauthorized")` 客户端精确收到 code 与 reason。

| code | 场景 | `reason` |
|---|---|---|
| `4401` | 无有效会话 cookie、`Host` 或 `Origin` 校验失败 | `unauthorized` |
| `4404` | 目标不存在：`member` 不在名册、tmux 会话不存在 | `member-not-found` / `session-not-found` |
| `4503` | 暂不可用：工作区未登记、tmux 不可用、运行时未就绪 | `unavailable` |
| `1013` | 慢客户端降级到第三级(§5.6)；此时连接早已 `accept()`，照常送达 | — |

取 RFC6455 私有段 `4000-4999` 而不是笼统一个 `1008`：前端要按三种处置分流(跳重新认证 / 提示成员已消失 / 退避重连)，一个码分不出来。**控制面流(§5)与 WEB-007 的终端通道用同一套码**，前端只写一套判别。

**部署前提**：裸 `uvicorn` 没有 WebSocket 实现，握手直接返回 404、服务端日志打印 `No supported WebSocket library detected`,本节的一切都不会发生。默认依赖取 `uvicorn[standard]`,并且必须有一条**不经 `TestClient`** 的用例守住——Starlette `TestClient` 在进程内自实现 WS，绕开服务端 WS 库，测不出这个问题。

## 6. 认证会话与本机安全

### 6.1 为什么 loopback 还要凭证

只监听 `127.0.0.1` 挡住了局域网，但挡不住**同机的其他用户与其他进程**，也挡不住浏览器里任意网页向 `http://127.0.0.1:8787` 发跨站请求。控制台能读全部工作对话、任务与成员状态，将来还能控制 tmux，必须有凭证与来源校验。

### 6.2 会话建立

1. `amux web` 启动时生成 `secrets.token_urlsafe(32)`，**只存在于进程内存与启动时打印的 URL**，不落盘、不跨进程复用。终端打印 `http://127.0.0.1:8787/?token=<token>`。
2. 浏览器带 `?token=` 首次访问 → 服务端校验后 `Set-Cookie: amux_session=<会话id>; HttpOnly; SameSite=Strict; Path=/; Max-Age=<进程生命周期>`，并 **302 到不带 token 的 URL**(token 留在地址栏会进入历史记录与 referrer)。
3. 之后所有 API 与 WS 握手只认 cookie。
4. 进程退出即会话全部失效。

与 `gateway` 的 token 刻意不同：gateway 的 token 持久化在 `gateway.toml` 是因为手机要反复扫码接入；Web 控制台是本机短生命周期进程，持久化长期凭证只是多一个泄露面，没有收益。**不复用 `gateway.toml`，两者是不同信任域。**

### 6.3 每请求校验(全部必须实现)

- **`Host` 头**只接受 `127.0.0.1:<port>` 与 `localhost:<port>`，否则 401。防 DNS rebinding：攻击者把自己的域名解析到 `127.0.0.1` 就能让受害者浏览器带着 cookie 访问本地服务，Host 校验是标准防线。
- **WS 握手的 `Origin` 头**只接受 `http://127.0.0.1:<port>` 与 `http://localhost:<port>`，否则拒绝升级。WebSocket **不受同源策略与 CORS 保护**，任意网页都能对本地 WS 发起连接，Origin 校验是这里唯一的门。
- **不设置任何 CORS 响应头**。没有 `Access-Control-Allow-Origin`，跨站页面就读不到响应体。
- 写操作(WEB-006/008)另需自定义头 `X-Amux-Session: <会话id>`，与 SameSite cookie 构成双重提交。自定义头会触发预检，跨站页面在没有 CORS 放行的前提下发不出去。缺失或不匹配一律 `unauthorized` + 401(§2.4)，**在业务校验之前拒绝**，不要先解析请求体。这条对**所有**写端点生效，WEB-008 的打断/终止/重启/接管与 WEB-006 的发消息/上传同等对待；新增写端点时挂同一个依赖，不要各写各的判断。
- **可写 WS 通道走一次性票据**(WEB-008 定案，2026-08-30)。浏览器的 `WebSocket` 构造函数**不能设置自定义请求头**，所以双提交头这条对 WS 握手用不上。可写通道(接管、直连输入等能改变成员状态的通道)改为：先由一个受双提交头保护的 HTTP 端点签发票据，WS 建连后**在首帧**把票据交上来，服务端消费后才进入可写态。硬性要求，缺一条这层就形同虚设：
  - 票据由 CSPRNG 生成，至少 128 位熵；**绑定 `actor` 与目标 `member`**，服务端按票据里的绑定校验本次连接要操作的成员，不看客户端在帧里自报的成员。
  - **一次性**：消费即失效，且消费必须是原子的(并发两条连接拿同一张票，只能有一条成功)。
  - **短时效**：签发到消费不超过 30 秒，够开一个 socket 即可。
  - **只走首帧，不进 URL**。query string 会进服务端日志、浏览器历史与 `Referer`，票据一旦落到那里就等于长期凭证。
  - 首帧不是合法票据(或超时未送达)一律 `close(4401, "unauthorized")`(§5.7)。
  - 票据**不替代**任何既有校验：cookie、`Host`、`Origin` 照常验，票据是在它们之上多一层授权绑定，不是它们的替身。
  - 只读通道(`/api/v1/stream`、镜像观看)不需要票据，维持 cookie + `Origin`。

### 6.4 actor 与不可信输入

- 请求的 actor 恒为会话身份(首版 `human`)，**不读请求体里的 actor 字段**；出现该字段直接 400 `invalid-request`，不静默忽略 —— 静默忽略会让客户端误以为自报生效。
- 路径、tmux target、成员名、任务 ID 全部按领域校验器过一遍(`validate_task_id`、成员名须在名册/adopt 集合内)，不拼接进任何命令行。
- 任务权限声明(谁是 leader / assignee / reviewer)只信账本投影，不信客户端。

### 6.5 WEB-006 消息与附件

三个端点均要求有效会话 cookie；两个 `POST` 还必须带
`X-Amux-Session: <session.write_token>`，否则返回 `unauthorized/401`。

- `POST /api/v1/attachments`：请求 body 是原始图片字节，`Content-Type` 必须为
  `image/*`。服务端实际解码、检查 20 MiB/4000 万像素上限，再规范化为 PNG，写入
  工作区既有 `attachments/clipboard-<sha256前16位>.png` 内容寻址存储（0600）。响应只给
  `{"attachment":{"id","name","media_type","width","height","size","download_url"}}`；
  不返回、也不接受任何本机路径。同一内容重复上传返回同一 id。
- `GET /api/v1/attachments/{id}`：只接受 16 位小写十六进制内容 id，从当前工作区附件根
  精确解析后返回 `image/png`；响应头仅含安全文件名，不含绝对路径。
- `POST /api/v1/messages`：JSON 字段白名单为
  `to/text/kind/task_id/reply_to/attachment_ids`。普通消息 `kind=message`；省略 `to` 时
  服务端从当前绑定团队取唯一 Leader。`kind=ask` 不得带 `task_id`；`kind=reply` 必须带
  `reply_to`，不得带 `to/task_id`，收件人由服务端读取原 ask 索引反查。图片单独发送时
  服务端沿用 TUI 文案补成“请查看附加图片。”。所有路径都经 `Message.create()` +
  `deposit()`，ask/reply 同时用既有 `store_ask()` / `store_reply()` 保证关联与首答语义。

请求出现 `actor` / `from` / `path` 或任何未知字段直接返回 `invalid-request/400`；消息
`from` 恒取认证会话 actor（首版 `human`）。`attachment_ids` 在服务端解析成冻结总线 schema
需要的绝对本机路径，浏览器始终只持有 id。待发图片撤销只移除客户端引用，不删除可被其他
消息复用的内容寻址文件。SPA 在任务视图把当前选中 task 关联到普通消息；ask/reply 模式清除
task，显式 `@成员` 覆盖目标，成功发送后记住上次对象。

## 7. 对 inventory §2 的覆盖对照

WEB-003 要求覆盖的 12 项与 WEB-004 的实时验收项，在本文中的落点：

| inventory 条目 | 端点 / 帧 |
|---|---|
| 顶部 Header / 工作区副标题 | §4.4 `/workspace` |
| 左栏任务摘要 | §4.6 `summary` |
| 左栏工作对话记录卡(含未读数) | §4.8 `head_seq` + §4.9 客户端计算 + `timeline` delta |
| 左栏成员卡片 | §4.10 `/members` + `member` invalidation |
| 左栏任务列表 | §4.6 `tasks` + `work` invalidation(tasks) |
| 中栏任务详情 | §4.7 `/work/tasks/{id}` |
| 任务证据 | §4.7 `task.evidence` |
| 不可覆盖任务事件流 | §4.7 `events` + `work` delta(append) |
| 任务关联工作对话 | §4.7 `communications` |
| 工作对话记录时间线 | §4.8 + `timeline` delta(append/update) |
| 健康告警 | §4.11 + `health` delta(raise/clear) |
| 成员状态实时更新 | §4.10 + `member` invalidation(0.5s，仅变化时) |

WEB-004 要求"以测试客户端验证，不依赖 SPA"：上述全部形态都是 JSON，可用 `httpx` + `websockets` 直接断言，不需要浏览器。

## 8. 明确不在本文范围

- 消息发送、ask/reply、附件上传(WEB-006)
- tmux 镜像帧通道、点击直连、完整接管 PTY 桥(WEB-007)
- 成员生命周期与危险动作确认(WEB-008)
- 交互租约与 Hub 投递租约的协议(WEB-002)；本文只承诺只读 API 不碰租约
- 局域网/公网、TLS、登录(另立 Goal)

## 9. 已识别的现状风险(留给 WEB-001 / WEB-003 处理)

1. **审计日志时间无时区**(`bus/log.jsonl` 的朴素本机时间)。跨机器读同一份日志、或服务端与浏览器时区不同都会错位。本文用服务端归一化的 `*_at` 绕开，但根因在总线 schema。schema 是冻结契约，只能加可选字段 —— 若将来要修，是新增可选 `ts_utc` 而非改 `ts`。
2. **单调时钟值混在状态快照里**(`ActivitySnapshot.last_output_at`)。WEB-001 下沉成员状态时若原样搬进 DTO，浏览器会拿到一个无意义的数。已在 §4.10 给出替代口径。
3. **TUI 把"看镜像"和"改 tmux 窗口尺寸"绑在一起**。控制面下沉时必须拆成两个动作，否则 Web 侧一打开成员视图就会和 TUI 抢 resize(WEB-002 要解决的正是这个)。
4. **`event.data` 是自由字典**。若控制面 DTO 全量透传，未来账本新增内部字段会自动流到浏览器。本文定的是白名单取值(§4.7)。
5. **未读数若做成服务端状态会随多端漂移**。两个 tab 各自的"已读位置"不同，服务端存一份必然错。已定为客户端状态(§4.9)。

## 10. 待 WEB-001 合入后校正的清单

以下字段按当前 `console` / 领域层现状起草，控制面落地后需逐项对齐，以控制面输出为准：

- [x] `MemberCardSnapshot` 输出 `silent_for`、`alive` 与 `source`，聚合 `MemberSnapshotView` 输出 `snapshot_at`；不含 `relative_activity()` 字符串或 `last_output_at`
- [x] `TimelineEntry` 输出归一化 `at`、原串 `ts`、`seq`、`key` 与 `has_body`；`TimelineProjector` 统一分配实时序号，不含 `member_color()`
- [x] `TaskSummaryView` 直接给出 active/waiting/blocked/total 与完整 `by_status`
- [x] `TaskEventView.details` 只按 `DETAIL_FIELDS` 白名单输出结构化 key→value
- [x] `selected_default_task_id()` 与 `task_board_view().selected_default` 已下沉
- [x] `task_communications()` 在控制面按 task + deposit 过滤并截取最近 20 条
- [x] 词表归属：任务状态/事件标签/详情字段留在 `work` 领域层；任务/成员图形、时间线分类与结局语义位于 `control.vocabulary`；颜色仍只在前端主题层
