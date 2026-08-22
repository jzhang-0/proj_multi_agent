"""ROS-006/007:协作协议单一来源，并生成通用成员开场白。"""

from __future__ import annotations

from pathlib import Path

import pytest

from roster import load_roster
from roster.paths import repo_root
from roster.protocol import (
    COLLABORATION_PROTOCOL_HEADING,
    ProtocolSourceError,
    check_single_source,
    extract_collaboration_protocol,
    load_collaboration_protocol,
    render_member_greeting,
)
from roster.start import window_command


def test_extracts_exact_protocol_section_without_following_rules() -> None:
    markdown = """# repo

## amux 协作协议

身份规则。

- 发送规则。

## 工作规则

不要混进来。
"""
    assert extract_collaboration_protocol(markdown) == (
        "## amux 协作协议\n\n身份规则。\n\n- 发送规则。"
    )


@pytest.mark.parametrize(
    "markdown",
    [
        "# repo\n\n## 工作规则\n",
        "## amux 协作协议\n\n## 工作规则\n",
        "## amux 协作协议\n规则一\n\n## amux 协作协议\n规则二\n",
    ],
)
def test_missing_empty_or_duplicate_protocol_is_rejected(markdown: str) -> None:
    with pytest.raises(ProtocolSourceError):
        extract_collaboration_protocol(markdown)


def test_repository_roster_and_agents_pass_single_source_check() -> None:
    check_single_source()
    roster_text = (repo_root() / "roster.toml").read_text(encoding="utf-8")
    assert "default_greeting_template" not in roster_text
    assert "greeting_template" not in roster_text


def test_checker_rejects_protocol_copy_in_roster(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("## amux 协作协议\n\n发消息用 amux msg。\n", encoding="utf-8")
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
    protocol = load_collaboration_protocol()
    roster = load_roster()
    for member in roster.members:
        greeting = member.render_greeting()
        assert greeting.count(COLLABORATION_PROTOCOL_HEADING) == 1
        assert protocol in greeting
        assert member.name in greeting
        assert protocol in window_command(member)


def test_changing_agents_protocol_changes_greeting_without_touching_roster(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("## amux 协作协议\n\n第一版。\n", encoding="utf-8")
    first = render_member_greeting(
        "codex", protocol=load_collaboration_protocol(agents)
    )

    agents.write_text("## amux 协作协议\n\n第二版。\n", encoding="utf-8")
    second = render_member_greeting(
        "codex", protocol=load_collaboration_protocol(agents)
    )

    assert "第一版" in first and "第二版" not in first
    assert "第二版" in second and "第一版" not in second


def test_optional_member_intro_cannot_replace_protocol() -> None:
    greeting = render_member_greeting(
        "codex",
        intro_template="你好 {name}",
        protocol="## amux 协作协议\n\n权威规则。",
    )
    assert greeting.startswith("你好 codex")
    assert "## amux 协作协议\n\n权威规则。" in greeting


def test_greeting_includes_workspace_and_project_root() -> None:
    greeting = render_member_greeting(
        "claude",
        protocol="## amux 协作协议\n\n用 amux msg。",
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert "你现在在工作区 demo,项目根是 /tmp/demo。" in greeting
    assert "你是 amux 工作区的协作成员 claude。" in greeting
    assert "用 amux msg。" in greeting


def test_repository_greeting_omits_stale_or_non_universal_rules() -> None:
    greeting = render_member_greeting(
        "sol",
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert "本机 AI 群" not in greeting
    assert "成员:`claude`" not in greeting
    assert "只在被 @ 时响应" not in greeting
    assert "不超过 6 轮" not in greeting
    assert "30 秒内最多发送 8 条" not in greeting
    assert "32KB" not in greeting
    assert "50 条未投递" not in greeting
    assert "[群消息] 来自 xxx:" in greeting
    assert "amux msg --reply" in greeting
    assert "只有 human 直接要求才能做" in greeting


def test_single_source_requires_amux_msg(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "## amux 协作协议\n\n在仓库根运行 `./msg` 发消息。\n",
        encoding="utf-8",
    )
    roster = tmp_path / "roster.toml"
    roster.write_text('[[members]]\nname = "bot"\ncommand = "cat"\n', encoding="utf-8")
    with pytest.raises(ProtocolSourceError, match="amux msg"):
        check_single_source(agents_path=agents, roster_path=roster)
