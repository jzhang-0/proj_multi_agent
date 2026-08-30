# Changelog

本文件记录 `amux-team` 公开发行的用户可见变更。开发中的 Goal 与验收证据见
[`docs/goals/`](docs/goals/README.md)。

## v0.2.0 — 2026-08-31

桌面 Web 控制台首版，与既有 TUI 共用同一工作区状态、任务账本与消息总线。

### 修复

- **成员终端镜像帧渲染错位(T-026，human 实机复现）**：tmux `capture-pane` 帧以裸 LF 分行，而镜像 xterm 此前 `convertEol:false`，每行起点不回列首，真实 Claude/Codex 画面呈阶梯状右移。现在镜像终端启用 `convertEol:true`（完整接管仍为原始 PTY 流，保持 false），并以真实 tmux 会话逐行断言 xterm 缓冲区与 `capture-pane` 一致。
- **成员终端镜像误报 unauthorized(T-025，human 实机复现）**：镜像 WebSocket
  非拒绝码断线重连（网络抖动、服务重启）后，浏览器前端此前不会清空"已持有
  交互租约"的本地状态；重连是全新服务端连接、租约需要重新获取，若此时窗口
  尺寸变化触发一次尺寸上报，服务端会把它当未持有租约的写操作直接拒绝，界面
  就此显示"无法连接成员终端 unauthorized"且不会自动恢复。现在重连时会同步
  清空本地租约状态，画面正确回到只读镜像。同时补上此前完全空白的 WebSocket
  握手/票据拒绝结构化日志，标注具体是 Host/Origin/cookie 哪一项校验失败。

### 新能力（WEB-001～011）

- **WEB-001** — 控制面读模型：任务/成员/工作对话/健康状态等 DTO 下沉到 `src/control/`，TUI 与 Web 共用。
- **WEB-002** — 多前端协调：每工作区 Hub 投递租约、每成员交互租约，TUI/Web 并列不重复投递或交错键入。
- **WEB-003** — `amux web` 后端：FastAPI/ASGI，默认 `127.0.0.1`，本机会话认证，snapshot API 与最小健康页。
- **WEB-004** — WebSocket 实时流：`epoch/revision` 断档全量 resync，慢客户端有界队列。
- **WEB-005** — 桌面 SPA：Preact + TypeScript + esbuild，只读导航闭环（任务/工作对话/帮助/主题等）。
- **WEB-006** — 消息与附件：工作对话输入、`@` 补全、ask/reply、浏览器图片粘贴与撤销。
- **WEB-007** — 终端镜像与受限直连：tmux 镜像帧、回滚、租约、ANSI 安全渲染、xterm.js 本地 bundle。
- **WEB-008** — 成员生命周期与完整接管：打断/终止/重启、`/up`/`/down`/`/adopt`/`/mute`、PTY attach，危险动作二次确认。
- **WEB-009** — 发布收口：前端产物进 wheel/sdist，`qa.release` 源码外安装验证包内 Web 静态资源。
- **WEB-010** — 时间线 seq 按到达顺序分配，前端排序用 `at` 而非 seq。
- **WEB-011** — 进程内统一终端采集源（`control.mirror`），TUI 与 Web 各自作为观看者接入。

### `amux web` 用法

```bash
amux web --port 8787
```

服务只监听 `127.0.0.1`；启动时终端打印带一次性 token 的地址，首次访问换发 `HttpOnly`、`SameSite=Strict` 会话 cookie。退出只停 Web 服务，不关闭成员 tmux 会话。详见 [README](README.md#启动-web-控制台)。

### 依赖变更

- 默认依赖使用 `uvicorn[standard]>=0.32`（含 WebSocket 支持），裸 `uvicorn` 无法提供 `/api/v1/stream` 握手。

### 安全边界

- Web 只监听本机；HTTP 校验 Host，WebSocket 额外校验 Origin；API 不接受客户端自报 actor/路径。
- 写端点使用 `X-Amux-Session` 双提交校验；可写 WebSocket 通道使用一次性票据首帧交票。
- 附件内容寻址存储，响应不泄露本机绝对路径；终端输出按安全终端数据渲染，禁止 `innerHTML`/CDN。
- 危险控制动作（终止、重启、完整接管等）需二次确认并写入审计。

### 已知限制

- **WEB-012 未做**：`uv run amux web` 从源码树启动且未先 `npm --prefix web run build` 时，浏览器可能只看到 WEB-003 占位健康页，而非完整 SPA。
- **REL-006 未做**：`qa.release` 尚未自动启动 Web 并断言 `/api/v1/stream` 的 101 握手，仍依赖人工或专项测试旁跑。
- **附件 500MB 上限**：工作区附件目录达到总容量上限会拒绝新上传，不做自动淘汰（历史消息可能仍引用旧附件）。
- **跨进程双采集**：TUI 与 `amux web` 是独立进程，同看一个成员时各自轮询 tmux，频率叠加；跨进程共享采集判定不做（见 `docs/web/terminal-protocol.md`）。

## v0.1.0 — 2026-08-23

首次公开发布：TUI 总控台、任务账本、消息总线、团队档案、PyPI 安装与 MIT 许可。
