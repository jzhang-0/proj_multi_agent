"""REL-001:发行元数据、资源同步与 wheel 回退路径。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from amux_runtime import PROTOCOL_RESOURCE, ROSTER_RESOURCE, read_resource
from roster.load import load_roster
from roster.protocol import extract_chat_protocol, load_chat_protocol

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_name_scripts_and_build_packages_are_release_ready() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    assert project["name"] == "amux-team"
    assert project["scripts"]["amux"] == "console.cli:main"
    assert project["requires-python"] == ">=3.11"
    assert "Development Status :: 3 - Alpha" in project["classifiers"]
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/amux_runtime" in packages


def test_release_workflow_has_version_guard_and_trusted_publishers() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'test "$GITHUB_REF_NAME" = "v$(uv version --short)"' in workflow
    assert "uv run python -m qa.release --out-dir dist" in workflow
    assert "repository-url: https://test.pypi.org/legacy/" in workflow
    assert workflow.count("id-token: write") == 2
    assert workflow.count("pypa/gh-action-pypi-publish@release/v1") == 2


def test_packaged_runtime_resources_match_source_authorities() -> None:
    source_roster = (ROOT / "roster.toml").read_text(encoding="utf-8").strip()
    packaged_roster = read_resource(ROSTER_RESOURCE).splitlines()
    assert "\n".join(packaged_roster[1:]).strip() == source_roster

    source_protocol = extract_chat_protocol(
        (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    )
    assert read_resource(PROTOCOL_RESOURCE).strip() == source_protocol


def test_roster_and_protocol_fall_back_to_packaged_resources(monkeypatch) -> None:
    monkeypatch.setattr("roster.load.source_default_path", lambda: None)
    monkeypatch.setattr("roster.protocol.source_root", lambda: None)

    roster = load_roster()
    protocol = load_chat_protocol()

    assert roster.source == "package:amux_runtime/roster.toml"
    assert [member.name for member in roster.members] == [
        "claude",
        "codex",
        "cursor",
        "agy",
    ]
    assert "成员由当前工作区的名册或团队档案决定" in protocol
