"""ROS-002: 四个真实成员的免弹窗适配档案。"""

from __future__ import annotations

import json

from roster.load import load_roster
from roster.paths import repo_root

EXPECTED_ARGS = {
    "claude": ("--permission-mode", "acceptEdits"),
    "codex": ("-s", "workspace-write", "-a", "on-request"),
    "cursor": ("--force",),
    "agy": ("--dangerously-skip-permissions", "-i"),
}

# 每家注释里必须同时出现「免弹窗手段」和「残留弹窗」两类信息的关键词。
COMMENT_MARKERS = {
    "claude": ("acceptEdits", "残留弹窗", ".claude/settings.json", "./msg"),
    "codex": ("workspace-write", "on-request", "残留弹窗"),
    "cursor": ("--force", "残留弹窗"),
    "agy": ("--dangerously-skip-permissions", "残留弹窗"),
}


def test_four_members_have_required_cli_flags() -> None:
    roster = load_roster()
    for name, args in EXPECTED_ARGS.items():
        member = roster.get(name)
        assert member is not None, name
        assert member.command in {"claude", "codex", "agent", "agy"}
        assert member.args == args
        assert member.enabled is True


def test_roster_comments_document_prompts_and_leftovers() -> None:
    text = (repo_root() / "roster.toml").read_text(encoding="utf-8")
    for name, markers in COMMENT_MARKERS.items():
        for marker in markers:
            assert marker in text, f"{name} 档案缺少注释标记 {marker!r}"


def test_claude_project_allowlist_permits_msg() -> None:
    settings = json.loads((repo_root() / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    assert any(rule.startswith("Bash(./msg") for rule in allow)
    assert any(rule.startswith("Bash(amux msg") for rule in allow)
