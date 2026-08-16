# IM 网关(GATE)— 远期

把手机上的群聊(IM)接到同一条总线:人在群里 @ 成员等价于本机 `./msg`,成员的发言回传群里署名显示。**平台已由 human 于 2026-08-16 拍板:自建**(候选里的飞书 / Discord 均需第三方账号与凭证;Telegram 早已排除——bot 看不到其他 bot 的消息)。自建的形态:本机跑一个轻量群聊网页 + 长轮询接口,手机在同一网络下用浏览器打开即可,不依赖任何第三方账号。

- [x] **GATE-001** — 网关抽象:`src/gateway/` 定义 adapter 接口(收群消息 → 入队 bus;订阅 bus → 发回群),与具体 IM 解耦;单 bot 模式(网关解析 @ 路由、代发署名),不要求每成员一个 bot 账号。
  - 前置:BUS-008;人拍板 IM 平台(2026-08-16 拍板:自建)。
  - 验证:`uv run ruff check . && uv run pytest tests/test_gateway_base.py -q`
  - 证据:`src/gateway/base.py`(`GatewayAdapter` 只要求 start/stop/post 三件事;`Gateway` 双向桥:`on_group_message` 路由后入队,`pump_once` 读审计日志把新流量推进群,`catch_up` 保证启动不把历史一股脑倒进群里,`deliver` 事件与 `deposit` 重复所以不转)、`src/gateway/router.py`(单 bot 模式:`@名字 正文` 路由、不写 @ 就按**该房间**上一个对话对象、代发署名 `claude: → codex: …`、远程身份统一 `im:` 前缀且天然不等于 `human` 所以照样受限频);`bus.message.REMOTE_PREFIX` + `bus.hub.is_screen_only` 让 `im:` 收件人像 `human` 一样只上屏、不去 tmux 找会话(成员回给手机上的人不再被记成投递失败);`tests/test_gateway_base.py` 14 passed,含一条**扫描核心模块禁止出现平台名或传输细节**的解耦守卫测试,全量回归 318 passed。
- [ ] **GATE-002** — 第一个 adapter 实现(平台按人拍板结果),含凭证配置方式与断线重连。
  - 处理登记:claude,2026-08-16 07:55 +0800,`gate-002-claude`。
  - 前置:GATE-001。
- [ ] **GATE-003** — 端到端:手机群里 @ 成员派活、成员间协作、结果回到群里,全程不碰电脑实测一遍。
  - 前置:GATE-002、ROS-003。
  - 进行中:**未开工**,阻塞在 GATE-002;另一半前置 ROS-003 已完成。
- [ ] **GATE-004** — 网关安全:只服务白名单群与白名单用户;来自 IM 的消息标记来源(`from` 带 `im:` 前缀或等价机制)并同样过清洗与限频;危险操作指令即使来自 IM 的 human 也要求本机二次确认(远程指令弱于本机指令)。
  - 前置:GATE-002。
  - 进行中:**未开工**,阻塞在 GATE-002。本机侧的地基已经就绪:清洗(BUS-005)、限频与熔断(BUS-003/004)对任何发件人一视同仁,危险操作的「只认 human 直接指令」写在 [AGENTS.md 群聊协议](../../AGENTS.md#群聊协议)里;网关落地时在其之上再加白名单与远程指令降权。
