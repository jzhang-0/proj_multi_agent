"""颜色 token 与深浅两套主题。

界面里**所有**颜色都从这里取,组件里不再写死十六进制。这样换主题只改一
处,而且"成员色 / 状态色 / 强调色"三组能被一眼看全、一起调。

两套主题的取色原则:

- 深色下用中高亮度的中间调,浅色下换成同色系的深色,保证在白底上也有
  足够对比;
- 状态色在两套主题里保持"同一种状态同一个色相"(空闲蓝、工作青、卡住
  黄、掉线红、失败品红),换主题只改明度,人不用重新学一遍;
- 状态**不只靠颜色**区分:形状(○▶◐✕‼)由 `STATUS_GLYPHS` 定义,黑白
  终端和色盲用户照样读得出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control.vocabulary import MEMBER_STATE_GLYPHS

# 兼容既有 console 导出；词表事实来源已下沉，颜色仍在本模块。
STATUS_GLYPHS = MEMBER_STATE_GLYPHS


@dataclass(frozen=True)
class Tokens:
    """一套主题的全部颜色 token。"""

    name: str
    dark: bool
    #: 成员正文色池,按名字 crc32 取模选,同名永远同色
    members: tuple[str, ...]
    #: 状态色:key 与 `STATUS_GLYPHS` 一一对应
    status: dict[str, str]
    #: 保留身份色
    human: str
    bus: str
    #: 强调、次要文字、分隔线
    accent: str
    accent_text: str
    muted: str
    divider: str
    #: Textual 主题用的底色系
    background: str
    surface: str
    panel: str
    foreground: str
    extra: dict[str, str] = field(default_factory=dict)


DARK = Tokens(
    name="console-dark",
    dark=True,
    members=("#5fd7ff", "#ffaf5f", "#87ff87", "#ff87d7", "#d7d75f", "#87afff"),
    status={
        "idle": "#87afff",
        "working": "#5fd7ff",
        "stuck": "#ffd75f",
        "dead": "#ff5f5f",
        "failed": "#ff00af",
    },
    human="#ffd75f",
    bus="#9e9e9e",
    accent="#005f87",
    accent_text="#ffffff",
    muted="#9e9e9e",
    divider="#5a5a5a",
    background="#121212",
    surface="#1e1e1e",
    panel="#272727",
    foreground="#e0e0e0",
)

LIGHT = Tokens(
    name="console-light",
    dark=False,
    members=("#005f87", "#af5f00", "#00875f", "#af005f", "#87651a", "#0055af"),
    status={
        "idle": "#0055af",
        "working": "#005f87",
        "stuck": "#875f00",
        "dead": "#af0000",
        "failed": "#af005f",
    },
    human="#875f00",
    bus="#6c6c6c",
    accent="#cfe6ff",
    accent_text="#003049",
    muted="#5f5f5f",
    divider="#9e9e9e",
    background="#fdfdfd",
    surface="#f2f2f2",
    panel="#e6e6e6",
    foreground="#1c1c1c",
)

THEMES: dict[str, Tokens] = {DARK.name: DARK, LIGHT.name: LIGHT}

_active: Tokens = DARK


def tokens() -> Tokens:
    """当前生效的一套 token。组件渲染时现取,换主题后重画即可跟上。"""
    return _active


def use(name: str) -> Tokens:
    """切换主题;名字不认识就原样不动。"""
    global _active
    _active = THEMES.get(name, _active)
    return _active


def toggle() -> Tokens:
    """深 ↔ 浅。"""
    return use(LIGHT.name if _active.dark else DARK.name)


def status_color(state: str) -> str:
    return tokens().status.get(state, tokens().muted)


def status_presentation(state: str) -> tuple[str, str, str]:
    """(图形, 短标签, 颜色);未知状态也给一组能显示的值。"""
    glyph, label = STATUS_GLYPHS.get(state, ("?", state.upper()[:5]))
    return glyph, label, status_color(state)
