"""本机 Web 控制台后端。

依据 [Web API 与实时协议设计稿](../../docs/web/api-protocol.md)：

- `web.auth`:本机认证会话——进程内 token 换 `HttpOnly`/`SameSite=Strict`
  cookie,进程退出即全部失效。
- `web.app`:FastAPI 应用工厂,Host 校验、不设 CORS、snapshot 路由与
  `/api/v1/stream` WebSocket。
- `web.snapshots` / `web.state`:epoch/revision 与控制面 DTO。
- `web.stream`:versioned 实时事件流(WEB-004)。
- `web.cli`:`amux web` 入口,只监听 `127.0.0.1`。
"""
