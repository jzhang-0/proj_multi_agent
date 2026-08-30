"""本机 Web 控制台后端(WEB-003)。

首版范围只做与 WEB-001 下沉的控制面 DTO 无关的地基,依据是
[Web API 与实时协议设计稿 §2/§6](../../docs/web/api-protocol.md):

- `web.auth`:本机认证会话——进程内 token 换 `HttpOnly`/`SameSite=Strict`
  cookie,进程退出即全部失效,不落盘、不跨进程复用。
- `web.app`:FastAPI 应用工厂,接入 `Host` 头校验、不设 CORS 响应头、
  `importlib.resources` 提供的最小静态健康页。
- `web.cli`:`amux web` 入口,起 uvicorn,只监听 `127.0.0.1`。

workspace/team/work/timeline/member 的 snapshot 端点依赖 WEB-001 的控制面
输出,尚未接入;`web.app.create_app()` 目前只挂根路径与鉴权,不建第二套
状态库。
"""
