"""CON-009:颜色 token 化、深浅两套主题、中英文混排按显示宽度对齐。"""

import asyncio
import pathlib
import re

import pytest
from rich.console import Console

import console
from bus.paths import BusPaths
from console import theme
from console.app import ConsoleApp
from console.layout import display_width, pad, truncate
from console.theme import DARK, LIGHT, STATUS_GLYPHS, THEMES
from console.timeline import TimelineEntry, member_color, render_entry
from console.widgets import Timeline

RICH = Console()


@pytest.fixture(autouse=True)
def restore_theme():
    """测试可能切主题,跑完恢复默认,免得污染别的用例。"""
    yield
    theme.use(DARK.name)


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def run_async(factory):
    return asyncio.run(factory())


# --- token 化 -----------------------------------------------------------


HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")


def test_no_hardcoded_colors_left_outside_the_theme():
    """颜色只能来自 theme:其余组件源码里不该再出现十六进制色号。"""
    root = pathlib.Path(console.__file__).parent
    offenders = {}
    for path in sorted(root.glob("*.py")):
        if path.name == "theme.py":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [line.strip() for line in lines if HEX_COLOR.search(line)]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"这些地方还写死了颜色:{offenders}"


def test_every_status_has_a_glyph_and_a_color_in_both_themes():
    for state in STATUS_GLYPHS:
        glyph, label = STATUS_GLYPHS[state]
        assert glyph and label
        for token_set in THEMES.values():
            assert token_set.status[state].startswith("#")


def test_status_is_distinguishable_without_color():
    glyphs = {glyph for glyph, _ in STATUS_GLYPHS.values()}
    labels = {label for _, label in STATUS_GLYPHS.values()}
    assert len(glyphs) == len(STATUS_GLYPHS)  # 形状各不相同
    assert len(labels) == len(STATUS_GLYPHS)  # 文字也各不相同


# --- 深浅两套 -----------------------------------------------------------


def test_switching_theme_changes_the_colors_but_not_the_identity():
    theme.use(DARK.name)
    dark_claude = member_color("claude")
    dark_index = DARK.members.index(dark_claude)

    theme.use(LIGHT.name)
    light_claude = member_color("claude")
    assert light_claude != dark_claude  # 颜色换了
    assert LIGHT.members.index(light_claude) == dark_index  # "谁是几号色"没换


def test_light_theme_is_used_for_rendering_after_toggle():
    theme.use(DARK.name)
    entry = TimelineEntry("2026-08-16 09:00:00", "human", "codex", "换主题", "delivered")
    dark_styles = {str(segment.style) for segment in render_entry(entry).render(RICH)}
    theme.toggle()
    light_styles = {str(segment.style) for segment in render_entry(entry).render(RICH)}
    assert DARK.human.lower() in " ".join(dark_styles).lower()
    assert LIGHT.human.lower() in " ".join(light_styles).lower()


def test_toggle_key_redraws_existing_timeline_lines(paths):
    app = ConsoleApp(paths, deliver=lambda message: True, members=("claude",))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            timeline = app.query_one("#timeline", Timeline)
            timeline.add(TimelineEntry("2026-08-16 09:00:00", "human", "codex", "换主题前说的话"))
            await pilot.pause()
            before = _styles_on_screen(timeline)

            await pilot.press("t")
            await pilot.pause()

            assert app.theme == LIGHT.name
            after = _styles_on_screen(timeline)
            assert "换主题前说的话" in _text_on_screen(timeline)  # 内容没丢
            assert before != after  # 颜色真的重画了

    run_async(scenario)


def _styles_on_screen(timeline):
    return [str(segment.style) for line in timeline.lines for segment in line]


def _text_on_screen(timeline):
    return "\n".join("".join(segment.text for segment in line) for line in timeline.lines)


# --- 宽字符对齐 ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,width",
    [("claude", 6), ("成员", 4), ("中英mix", 7), ("", 0), ("…", 1)],
)
def test_display_width_counts_cjk_as_two(text, width):
    assert display_width(text) == width


def test_pad_aligns_mixed_text_to_the_same_column():
    rows = ["claude", "成员名", "mix混排"]
    padded = [pad(row, 12) for row in rows]
    assert {display_width(row) for row in padded} == {12}


def test_truncate_never_splits_a_wide_char():
    assert truncate("一二三四五", 6) == "一二…"
    assert display_width(truncate("一二三四五", 6)) <= 6
    assert truncate("short", 10) == "short"
