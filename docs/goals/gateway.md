# IM 网关(GATE)— 远期

把手机上的群聊(IM)接到同一条总线:人在群里 @ 成员等价于本机 `./msg`,成员的发言回传群里署名显示。**平台已由 human 于 2026-08-16 拍板:自建**(候选里的飞书 / Discord 均需第三方账号与凭证;Telegram 早已排除——bot 看不到其他 bot 的消息)。自建的形态:本机跑一个轻量群聊网页 + 长轮询接口,手机在同一网络下用浏览器打开即可,不依赖任何第三方账号。

- [x] **GATE-001** — 网关抽象:`src/gateway/` 定义 adapter 接口(收群消息 → 入队 bus;订阅 bus → 发回群),与具体 IM 解耦;单 bot 模式(网关解析 @ 路由、代发署名),不要求每成员一个 bot 账号。
  - 前置:BUS-008;人拍板 IM 平台(2026-08-16 拍板:自建)。
  - 验证:`uv run ruff check . && uv run pytest tests/test_gateway_base.py -q`
  - 证据:`src/gateway/base.py`(`GatewayAdapter` 只要求 start/stop/post 三件事;`Gateway` 双向桥:`on_group_message` 路由后入队,`pump_once` 读审计日志把新流量推进群,`catch_up` 保证启动不把历史一股脑倒进群里,`deliver` 事件与 `deposit` 重复所以不转)、`src/gateway/router.py`(单 bot 模式:`@名字 正文` 路由、不写 @ 就按**该房间**上一个对话对象、代发署名 `claude: → codex: …`、远程身份统一 `im:` 前缀且天然不等于 `human` 所以照样受限频);`bus.message.REMOTE_PREFIX` + `bus.hub.is_screen_only` 让 `im:` 收件人像 `human` 一样只上屏、不去 tmux 找会话(成员回给手机上的人不再被记成投递失败);`tests/test_gateway_base.py` 14 passed,含一条**扫描核心模块禁止出现平台名或传输细节**的解耦守卫测试,全量回归 318 passed。
- [x] **GATE-002** — 第一个 adapter 实现(平台按人拍板结果),含凭证配置方式与断线重连。
  - 前置:GATE-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_gateway_local.py -q`;真跑:`GATEWAY_TOKEN=… GATEWAY_PORT=8799 uv run python -m gateway --bus-root <临时目录>`
  - 证据:`src/gateway/local.py`(`LocalChatAdapter`:标准库 `ThreadingHTTPServer`,`GET /` 发页面、`GET /api/messages?since=&token=` 长轮询、`POST /api/send` 收消息;内存里留 500 条历史供补齐)、`src/gateway/page.py`(手机端单文件页面,零外部资源)、`src/gateway/config.py`(**凭证配置**:`gateway.toml` + `GATEWAY_TOKEN/PORT/HOST/ROOM` 环境变量覆盖,第一次跑随机生成 token 并落盘,`gateway.toml` 已 gitignore)、`src/gateway/__main__.py`(打印手机可用的局域网地址);**断线重连**做在游标续传上:客户端记 `cursor`,重连带上就补齐断线期间的消息,页面侧 1→5 秒退避重试、回前台立刻重连;`tests/test_gateway_local.py` 10 passed(口令双向校验、空消息拒收、页面无外部依赖、断线后按游标补齐两条、长轮询来消息即返回、历史有界、token 生成与持久化、环境变量覆盖),全量回归 328 passed。
  - 真机路径实测:2026-08-16 起在 127.0.0.1:8799,`POST /api/send {"user":"小明","text":"@claude 手机上派个活"}` → 队列里出现 `{"to":"claude","from":"im:小明","text":"手机上派个活",…}`;`GET /api/messages` 回 `im:小明: → claude: 手机上派个活`,单 bot 署名与游标都对。
- [x] **GATE-004** — 网关安全:只服务白名单群与白名单用户;来自 IM 的消息标记来源(`from` 带 `im:` 前缀或等价机制)并同样过清洗与限频;危险操作指令即使来自 IM 的 human 也要求本机二次确认(远程指令弱于本机指令)。
  - 前置:GATE-002。
  - 验证:`uv run ruff check . && uv run pytest tests/test_gateway_security.py -q`;本机侧:`uv run python -m gateway pending|approve <编号>|reject <编号>`
  - 证据:`src/gateway/security.py` 三件事——① `SecurityPolicy` 白名单房间与用户,**空 users 一律拒绝**并明说去哪儿加(口令只证明"知道口令",不证明"该被服务");② 来源标记沿用 `im:` 前缀,因此它不是 `human`,清洗(BUS-005)与限频(BUS-003)照常生效;③ `danger_in` 识别 push / 删文件 / 装软件 / 出仓库 / sudo 私钥五类危险指令,命中就不入队,改由 `PendingStore` **落盘**挂起(确认命令是另一个进程,状态必须在盘上),同时给本机 `human` 发一条带编号的提醒,本机 `approve` 后网关下一轮才真正转给成员、并回群里说一声。`src/gateway/base.py` 按 白名单 → 路由 → 危险降权 → 入队 的顺序串起来;`src/gateway/config.py` 加 `users`/`rooms`(支持 `GATEWAY_USERS`);`__main__` 加 `pending/approve/reject` 子命令。`tests/test_gateway_security.py` 17 passed(白名单用户/房间/空名单、来源标记 + 第 9 条被限频拒收、转义序列被清洗、五类危险指令识别与正常指令不误伤、挂起不入队、放行后身份不变、拒绝即丢弃、**自称 human 也不能绕过**、挂起可审计、跨进程持久化),全量回归 345 passed。
  - 顺带:默认拒绝是新的默认值,GATE-001 的路由测试原本依赖"没配白名单也放行",已改成显式放行测试用户;架构 §4 与 README 已写明"远程指令弱于本机指令"的落地方式。
  - 备注:本机侧的地基已经就绪:清洗(BUS-005)、限频与熔断(BUS-003/004)对任何发件人一视同仁,危险操作的「只认 human 直接指令」写在 [AGENTS.md amux 协作协议](../../AGENTS.md#amux-协作协议)里;网关落地时在其之上再加白名单与远程指令降权。
