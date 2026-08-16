"""IM 网关:把手机上的群聊接到同一条总线。

平台已拍板**自建**(见架构决策 §2):本机起一个只用标准库的 HTTP 服务,
手机在同一网络下用浏览器打开就是群聊页。

- `base`:与具体 IM 无关的 adapter 接口与双向桥 `Gateway`;
- `router`:单 bot 模式的 @ 路由与代发署名,远程身份统一 `im:` 前缀。
"""

from gateway.base import Gateway, GatewayAdapter, GroupMessage, GroupPost
from gateway.router import IM_PREFIX, Route, display_name, is_from_im, sender_name

__all__ = [
    "IM_PREFIX",
    "Gateway",
    "GatewayAdapter",
    "GroupMessage",
    "GroupPost",
    "Route",
    "display_name",
    "is_from_im",
    "sender_name",
]
