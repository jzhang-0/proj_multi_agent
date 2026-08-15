"""ROS-006:群聊协议只来自 AGENTS.md，成员开场白由它生成。"""

from __future__ import annotations

from pathlib import Path

import pytest

from roster import load_roster
from roster.paths import repo_root
from roster.protocol import (
    CHAT_PROTOCOL_HEADING,
    ProtocolSourceError,
    check_single_source,
    extract_chat_protocol,
    load_chat_protocol,
    render_member_greeting,
)
from roster.start import window_command


def test_extracts_exact_protocol_section_without_following_rules() -> None:
    markdown = """# repo

## 群聊协议

身份规则。

- 发送规则。

## 工作规则

不要混进来。
"""
    assert extract_chat_protocol(markdown) == "## 群聊协议\n\n身份规则。\n\n- 发送规则。"


@pytest.mark.parametrize(
    "markdown",
    [
        "# repo\n\n## 工作规则\n",
        "## 群聊协议\n\n## 工作规则\n",
        "## 群聊协议\n规则一\n\n## 群聊协议\n规则二\n",
    ],
)
def test_missing_empty_or_duplicate_protocol_is_rejected(markdown: str) -> None:
    with pytest.raises(ProtocolSourceError):
        extract_chat_protocol(markdown)


def test_repository_roster_and_agents_pass_single_source_check() -> None:
    check_single_source()
    roster_text = (repo_root() / "roster.toml").read_text(encoding="utf-8")
    assert "default_greeting_template" not in roster_text
    assert "greeting_template" not in roster_text


def test_checker_rejects_protocol_copy_in_roster(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("## 群聊协议\n\n唯一规则。\n", encoding="utf-8")
    roster = tmp_path / "roster.toml"
    roster.write_text(
        """default_greeting_template = "另一份规则"
[[members]]
name = "bot"
command = "cat"
""",
        encoding="utf-8",
    )
    with pytest.raises(ProtocolSourceError, match="不得维护"):
        check_single_source(agents_path=agents, roster_path=roster)


def test_all_repository_member_greetings_embed_exact_authoritative_protocol() -> None:
    protocol = load_chat_protocol()
    roster = load_roster()
    for member in roster.members:
        greeting = member.render_greeting()
        assert greeting.count(CHAT_PROTOCOL_HEADING) == 1
        assert protocol in greeting
        assert member.name in greeting
        assert protocol in window_command(member)


def test_changing_agents_protocol_changes_greeting_without_touching_roster(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("## 群聊协议\n\n第一版。\n", encoding="utf-8")
    first = render_member_greeting("codex", protocol=load_chat_protocol(agents))

    agents.write_text("## 群聊协议\n\n第二版。\n", encoding="utf-8")
    second = render_member_greeting("codex", protocol=load_chat_protocol(agents))

    assert "第一版" in first and "第二版" not in first
    assert "第二版" in second and "第一版" not in second


def test_optional_member_intro_cannot_replace_protocol() -> None:
    greeting = render_member_greeting(
        "codex",
        intro_template="你好 {name}",
        protocol="## 群聊协议\n\n权威规则。",
    )
    assert greeting.startswith("你好 codex")
    assert "## 群聊协议\n\n权威规则。" in greeting
