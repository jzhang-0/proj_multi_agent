"""ROS-006/007 + TEAM-006：提示词文件单一来源与按角色拼装。"""

from __future__ import annotations

from pathlib import Path

import pytest

from amux_runtime import PROMPT_RESOURCES
from roster import load_roster
from roster.paths import repo_root
from roster.protocol import (
    ProtocolSourceError,
    check_single_source,
    extract_collaboration_protocol,
    load_prompt,
    render_member_greeting,
)
from roster.schema import Member
from roster.start import window_command

PROMPT_DIR = repo_root() / "src" / "amux_runtime" / "prompts"


def _write_valid_prompts(directory: Path) -> None:
    directory.mkdir()
    (directory / "common.md").write_text(
        "{name} {workspace_slug} {project_root} [群消息] amux msg "
        "amux msg --reply human\n",
        encoding="utf-8",
    )
    (directory / "leader.md").write_text(
        "{team_id} {model} {responsibility} {team_roster} "
        "唯一 Leader 最终责任 验收 接管 human\n",
        encoding="utf-8",
    )
    (directory / "member.md").write_text(
        "{team_id} {leader_name} {model} {responsibility} Leader 证据 评审 "
        "不能代替 Leader 反复失败\n",
        encoding="utf-8",
    )


def test_prompt_directory_is_the_runtime_single_source() -> None:
    assert set(PROMPT_RESOURCES) == {"common", "leader", "member"}
    assert {path.name for path in PROMPT_DIR.glob("*.md")} == {
        "README.md",
        "common.md",
        "leader.md",
        "member.md",
    }
    check_single_source()
    assert not (repo_root() / "src" / "amux_runtime" / "protocol.md").exists()


def test_unbound_member_gets_only_common_prompt() -> None:
    greeting = render_member_greeting(
        "solo",
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert "你是 amux 协作成员 `solo`" in greeting
    assert "当前工作区：`demo`" in greeting
    assert "项目根：`/tmp/demo`" in greeting
    assert "## 通用协作协议" in greeting
    assert "## Leader 责任" not in greeting
    assert "## 成员责任" not in greeting


def test_leader_gets_common_and_leader_prompt_only() -> None:
    member = Member(
        name="fable",
        command="claude",
        team_id="fable-core",
        role="leader",
        leader_name="fable",
        model="Claude Fable 5",
        responsibility="拆解、推进、验收并承担最终责任",
        team_roster="  - `fable`: Leader\n  - `sol`: 实现与验证",
    )
    greeting = member.render_greeting(
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert "## 通用协作协议" in greeting
    assert "## Leader 责任" in greeting
    assert "## 成员责任" not in greeting
    assert "团队 `fable-core` 的唯一 Leader" in greeting
    assert "模型档案是 `Claude Fable 5`" in greeting
    assert "`sol`: 实现与验证" in greeting
    assert "简单任务不为制造流程而强行拆分或安排独立评审" in greeting
    assert "采用最小充分验证" in greeting
    assert "不得惯性扩成全量测试、重复调查或多人独立评审" in greeting
    assert "并明确升级原因" in greeting
    assert "轻量验证不等于放弃验收" in greeting
    assert "最终验收和结项只能由你决定" in greeting
    assert "亲自接管" in greeting


def test_member_gets_common_and_member_prompt_only() -> None:
    member = Member(
        name="sol",
        command="codex",
        team_id="fable-core",
        role="member",
        leader_name="fable",
        model="Sol",
        responsibility="独立实现与交叉验证",
        team_roster="  - `fable`: Leader\n  - `sol`: 实现与验证",
    )
    greeting = member.render_greeting(
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert "## 通用协作协议" in greeting
    assert "## 成员责任" in greeting
    assert "## Leader 责任" not in greeting
    assert "Leader 是 `fable`" in greeting
    assert "不能代替 Leader 宣布最终完成" in greeting
    assert "反复失败时，不要机械重试" in greeting


def test_repository_presets_remain_unbound_and_common_only() -> None:
    for member in load_roster().members:
        greeting = member.render_greeting(
            workspace_slug="demo",
            project_root="/tmp/demo",
        )
        assert member.role == ""
        assert "## 通用协作协议" in greeting
        assert "## Leader 责任" not in greeting
        assert "## 成员责任" not in greeting
        command = window_command(member)
        assert "## 通用协作协议" in command
        assert "## Leader 责任" not in command
        assert "## 成员责任" not in command


def test_optional_member_intro_is_only_a_prefix() -> None:
    greeting = render_member_greeting(
        "codex",
        intro_template="你好 {name}",
        workspace_slug="demo",
        project_root="/tmp/demo",
    )
    assert greeting.startswith("你好 codex\n\n# amux 协作上下文")
    assert "## 通用协作协议" in greeting


def test_invalid_role_or_unknown_placeholder_is_rejected() -> None:
    with pytest.raises(ProtocolSourceError, match="未知成员角色"):
        render_member_greeting("bot", role="reviewer")
    with pytest.raises(ProtocolSourceError, match="未知占位符"):
        render_member_greeting(
            "bot",
            protocol="{not_a_field}",
            workspace_slug="demo",
            project_root="/tmp/demo",
        )


def test_checker_rejects_missing_placeholder_and_stale_rule(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _write_valid_prompts(prompt_dir)
    roster = tmp_path / "roster.toml"
    roster.write_text('[[members]]\nname = "bot"\ncommand = "cat"\n', encoding="utf-8")

    (prompt_dir / "leader.md").write_text(
        "{team_id} {model} {team_roster} 唯一 Leader 最终责任 验收 接管 human\n",
        encoding="utf-8",
    )
    with pytest.raises(ProtocolSourceError, match="缺少占位符: responsibility"):
        check_single_source(prompt_dir=prompt_dir, roster_path=roster)

    _write_valid_prompts(tmp_path / "prompts-2")
    stale = tmp_path / "prompts-2"
    common = (stale / "common.md").read_text(encoding="utf-8")
    (stale / "common.md").write_text(
        common + "只在被 @ 时响应\n",
        encoding="utf-8",
    )
    with pytest.raises(ProtocolSourceError, match="含过时规则"):
        check_single_source(prompt_dir=stale, roster_path=roster)


def test_checker_rejects_prompt_copy_in_roster(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _write_valid_prompts(prompt_dir)
    roster = tmp_path / "roster.toml"
    roster.write_text(
        'default_greeting_template = "另一份规则"\n'
        '[[members]]\nname = "bot"\ncommand = "cat"\n',
        encoding="utf-8",
    )
    with pytest.raises(ProtocolSourceError, match="不得维护"):
        check_single_source(prompt_dir=prompt_dir, roster_path=roster)


def test_prompt_prose_is_not_copied_into_python_or_agents() -> None:
    leader = load_prompt("leader")
    member = load_prompt("member")
    implementation = (repo_root() / "src" / "roster" / "protocol.py").read_text(
        encoding="utf-8"
    )
    agents = (repo_root() / "AGENTS.md").read_text(encoding="utf-8")
    for sentence in ("最终责任都不能转移", "不能代替 Leader 宣布最终完成"):
        assert sentence in leader or sentence in member
        assert sentence not in implementation
        assert sentence not in agents


def test_legacy_agents_extractor_remains_available_but_is_not_runtime_source() -> None:
    markdown = "# repo\n\n## amux 协作协议\n\n旧调用兼容。\n\n## 其他\n"
    assert extract_collaboration_protocol(markdown) == (
        "## amux 协作协议\n\n旧调用兼容。"
    )
