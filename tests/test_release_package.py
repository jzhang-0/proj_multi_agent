"""REL-001/002:发行元数据、资源同步与隔离安装路径。"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

from amux_runtime import PROTOCOL_RESOURCE, ROSTER_RESOURCE, read_resource
from qa import release
from roster.load import load_roster
from roster.protocol import (
    extract_collaboration_protocol,
    load_collaboration_protocol,
)

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_name_scripts_and_build_packages_are_release_ready() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    assert project["name"] == "amux-team"
    assert project["scripts"]["amux"] == "console.cli:main"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["urls"] == {
        "Homepage": "https://github.com/jzhang-0/proj_multi_agent",
        "Repository": "https://github.com/jzhang-0/proj_multi_agent.git",
        "Issues": "https://github.com/jzhang-0/proj_multi_agent/issues",
    }
    assert "Development Status :: 3 - Alpha" in project["classifiers"]
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/amux_runtime" in packages
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 jzhang-0" in license_text


def test_release_workflow_has_version_guard_and_trusted_publishers() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'test "$GITHUB_REF_NAME" = "v$(uv version --short)"' in workflow
    assert "uv run python -m qa.release --out-dir dist" in workflow
    assert "--offline-smoke" not in workflow
    assert "repository-url: https://test.pypi.org/legacy/" in workflow
    assert workflow.count("id-token: write") == 2
    assert workflow.count("pypa/gh-action-pypi-publish@release/v1") == 2


def test_packaged_runtime_resources_match_source_authorities() -> None:
    source_roster = (ROOT / "roster.toml").read_text(encoding="utf-8").strip()
    packaged_roster = read_resource(ROSTER_RESOURCE).splitlines()
    assert "\n".join(packaged_roster[1:]).strip() == source_roster

    source_protocol = extract_collaboration_protocol(
        (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    )
    assert read_resource(PROTOCOL_RESOURCE).strip() == source_protocol


def test_roster_and_protocol_fall_back_to_packaged_resources(monkeypatch) -> None:
    monkeypatch.setattr("roster.load.source_default_path", lambda: None)
    monkeypatch.setattr("roster.protocol.source_root", lambda: None)

    roster = load_roster()
    protocol = load_collaboration_protocol()

    assert roster.source == "package:amux_runtime/roster.toml"
    assert [member.name for member in roster.members] == [
        "claude",
        "codex",
        "cursor",
        "agy",
    ]
    assert "具体角色、职责和协作对象以当前工作区" in protocol
    assert "只在被 @ 时响应" not in protocol


def _fake_release_runner(
    calls: list[tuple[list[str], dict[str, str]]], version: str
) -> Callable[..., CompletedProcess[str]]:
    def fake_run(
        argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        del cwd
        calls.append((argv, env or {}))
        stdout = ""
        if argv[-1] == "--version":
            stdout = f"amux {version}\n"
        elif argv[-3:] == ["member", "add", "claude"]:
            stdout = "已加入 claude\n"
        elif argv[-3:] == ["team", "show", "fable-core"]:
            stdout = "Claude Fable 5\n"
        return CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return fake_run


def test_strict_release_smoke_installs_and_imports_dependencies(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    wheel = tmp_path / "amux_team-0.1.0-py3-none-any.whl"
    monkeypatch.setattr(release.shutil, "which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(release, "_run", _fake_release_runner(calls, "0.1.0"))

    release._isolated_smoke(wheel, "0.1.0")

    install, install_env = next(
        (argv, env) for argv, env in calls if argv[1:3] == ["pip", "install"]
    )
    assert "--offline" not in install
    assert "--no-deps" not in install
    assert install_env["UV_CACHE_DIR"].endswith("/uv-cache")
    assert any(
        argv[-2:] == [
            "-c",
            "import textual, watchfiles; print('release dependencies ok')",
        ]
        for argv, _ in calls
    )


def test_offline_release_smoke_is_explicit_payload_only(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    wheel = tmp_path / "amux_team-0.1.0-py3-none-any.whl"
    monkeypatch.setattr(release.shutil, "which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(release, "_run", _fake_release_runner(calls, "0.1.0"))

    release._isolated_smoke(wheel, "0.1.0", offline=True)

    install = next(argv for argv, _ in calls if argv[1:3] == ["pip", "install"])
    assert "--offline" in install
    assert "--no-deps" in install
    assert not any("release dependencies ok" in " ".join(argv) for argv, _ in calls)
