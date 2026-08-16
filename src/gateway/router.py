"""单 bot 模式的路由与署名。

群里只有一个机器人账号,所以两件事必须由网关自己做:

- **解析 @ 路由**:`@claude 帮我看看` → 收件人 claude,正文"帮我看看";
  不写 @ 就发给这个房间上一个 @ 过的成员(和总控台输入框同一套规矩)。
- **代发署名**:群里发出去的每一行都带上"这句话是谁说的",否则四个成员
  的话混在一个 bot 账号下没法看。

发件人身份统一加 `im:` 前缀(`im:小明`),这样总线一眼能看出消息来自
远程:它不是 `human`,所以照样受限频约束;GATE-004 在这上面再加白名单
和危险指令降权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bus.hub import is_screen_only
from bus.message import REMOTE_PREFIX, Message
from bus.sanitize import sanitize

#: 来自 IM 的发件人前缀。定义在总线那边(`bus.message.REMOTE_PREFIX`),
#: 因为投递循环也要按它判断"这个收件人不投终端"
IM_PREFIX = REMOTE_PREFIX

#: 群里 `@名字 正文`
AT_ADDRESS = re.compile(r"^@([\w一-鿿-]+)\s*(.*)$", re.DOTALL)


def sender_name(user: str) -> str:
    """群里的用户名 → 总线里的发件人名。"""
    cleaned = sanitize(user).strip() or "anonymous"
    return f"{IM_PREFIX}{cleaned}"


def is_from_im(sender: str) -> bool:
    return sender.startswith(IM_PREFIX)


def is_gateway_recipient(name: str) -> bool:
    """收件人是不是"由网关代投"的远程身份(没有 tmux 会话,只上屏)。"""
    return name.startswith(IM_PREFIX) and is_screen_only(name)


@dataclass(frozen=True)
class Route:
    """一次路由的结果:要么有消息可入队,要么有一句给群里的错误说明。"""

    message: Message | None = None
    error: str = ""


def split_address(text: str) -> tuple[str | None, str]:
    match = AT_ADDRESS.match(text.strip())
    if match is None:
        return None, text.strip()
    return match.group(1), match.group(2).strip()


def route_group_message(
    user: str,
    text: str,
    *,
    members: tuple[str, ...],
    last_target: str | None = None,
) -> Route:
    """把群里的一条消息路由成一条总线消息。"""
    addressed, body = split_address(sanitize(text))
    if not body:
        return Route(error="消息是空的,写点内容再发")

    target = addressed or last_target
    if target is None:
        listed = "、".join(members) if members else "(名册是空的)"
        return Route(error=f"要先 @ 一个成员,群里现在有:{listed}")
    if members and target not in members and target != "human":
        listed = "、".join(members)
        return Route(error=f"没有成员叫 {target},群里现在有:{listed}")

    return Route(message=Message.create(target, body, sender=sender_name(user)))


def format_for_group(author: str, text: str, kind: str = "message") -> str:
    """群里看到的一行:代发署名 + 正文。"""
    mark = "· " if kind == "notice" else ""
    return f"{mark}{display_name(author)}: {text}"


def display_name(sender: str) -> str:
    """`im:小明` 在群里显示成 `小明(手机)`,本机成员显示原名。"""
    if is_from_im(sender):
        return f"{sender[len(IM_PREFIX):]}(手机)"
    return sender
