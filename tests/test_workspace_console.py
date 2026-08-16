"""WS-007:控制台按工作区绑定——cwd / --workspace / 标题 / 成员栏 / 时间线 / /workspace。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import ListView

from bus import Message, deposit
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.cli import bind_runtime
from console.cli import main as console_main
from console.commands import COMMAND_NAMES, CommandRunner
from console.compose import ComposeInput
from console.members import MemberStatusService, member_names
from console.widgets import Timeline
from workspace.errors import WorkspaceNotFound
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store


def events_text(app: ConsoleApp) -> str:
    log = app.query_one("#timeline", Timeline)
    return "\n".join("".join(segment.text for segment in line) for line in log.lines)


def make_project(root: Path, name: str, enabled: list[str]) -> Path:
    project = root / "projects" / name
    project.mkdir(parents=True)
    (project / "amux.toml").write_text(
        "enabled = [" + ", ".join(f'"{item}"' for item in enabled) + "]\n",
        encoding="utf-8",
    )
    return project


@pytest.fixture
def two_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Store, object, object]:
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    store = Store.default()
    alpha_root = make_project(tmp_path, "alpha", ["claude", "codex"])
    beta_root = make_project(tmp_path, "beta", ["cursor"])
    alpha, _ = store.add(alpha_root, slug="alpha")
    beta, _ = store.add(beta_root, slug="beta")
    return store, alpha, beta


def test_bind_runtime_uses_cwd_workspace(two_workspaces: tuple) -> None:
    _store, alpha, _beta = two_workspaces
    nested = alpha.project_root / "nested"
    nested.mkdir()
    paths, workspace = bind_runtime(cwd=nested)
    assert workspace is not None and workspace.slug == "alpha"
    assert paths.root == BusPaths.for_workspace(alpha).root
    assert paths.workspace == "alpha"


def test_bind_runtime_explicit_slug_beats_cwd(two_workspaces: tuple) -> None:
    _store, alpha, beta = two_workspaces
    paths, workspace = bind_runtime(slug="beta", cwd=alpha.project_root)
    assert workspace is not None and workspace.slug == "beta"
    assert paths.root == BusPaths.for_workspace(beta).root


def test_bind_runtime_unknown_slug_errors(two_workspaces: tuple) -> None:
    with pytest.raises(WorkspaceNotFound, match="没有叫"):
        bind_runtime(slug="ghost")


def test_bind_runtime_bus_root_still_wins(two_workspaces: tuple, tmp_path: Path) -> None:
    _store, _alpha, _beta = two_workspaces
    override = tmp_path / "explicit-bus"
    paths, workspace = bind_runtime(bus_root=str(override), slug="alpha")
    assert workspace is not None and workspace.slug == "alpha"
    assert paths.root == override.resolve()


def test_headless_workspace_flag_uses_that_bus(
    two_workspaces: tuple, capsys: pytest.CaptureFixture[str]
) -> None:
    _store, alpha, beta = two_workspaces
    deposit(
        Message.create("human", "只属于 alpha", sender="claude"),
        BusPaths.for_workspace(alpha).ensure(),
    )
    deposit(
        Message.create("human", "只属于 beta", sender="cursor"),
        BusPaths.for_workspace(beta).ensure(),
    )
    assert console_main(["--headless", "--once", "--workspace", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "只属于 alpha" in out
    assert "只属于 beta" not in out


def test_member_names_follow_workspace_amux_toml(two_workspaces: tuple) -> None:
    _store, alpha, beta = two_workspaces
    assert member_names(cwd=alpha.project_root) == ("claude", "codex")
    assert member_names(cwd=beta.project_root) == ("cursor",)


def test_title_and_members_and_timeline_are_workspace_scoped(
    two_workspaces: tuple,
) -> None:
    _store, alpha, beta = two_workspaces
    deposit(
        Message.create("human", "alpha 流量", sender="claude"),
        BusPaths.for_workspace(alpha).ensure(),
        audit=True,
    )
    deposit(
        Message.create("human", "beta 流量", sender="cursor"),
        BusPaths.for_workspace(beta).ensure(),
        audit=True,
    )

    app = ConsoleApp(
        BusPaths.for_workspace(alpha),
        workspace=alpha,
        deliver=lambda _message: True,
        pump_enabled=False,
        member_status=MemberStatusService(("claude", "codex")),
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)):
            assert app.sub_title == f"alpha · {alpha.project_root}"
            listing = app.query_one("#members", ListView)
            assert [str(item.id) for item in listing.children] == [
                "conv-timeline",
                "member-claude",
                "member-codex",
            ]
            text = events_text(app)
            assert "alpha 流量" in text
            assert "beta 流量" not in text

    asyncio.run(scenario())


def test_slash_workspace_switches_binding(two_workspaces: tuple) -> None:
    _store, alpha, beta = two_workspaces
    deposit(
        Message.create("human", "还在 alpha", sender="claude"),
        BusPaths.for_workspace(alpha).ensure(),
        audit=True,
    )
    deposit(
        Message.create("human", "已经到 beta", sender="cursor"),
        BusPaths.for_workspace(beta).ensure(),
        audit=True,
    )
    app = ConsoleApp(
        BusPaths.for_workspace(alpha),
        workspace=alpha,
        deliver=lambda _message: True,
        pump_enabled=False,
        member_status=MemberStatusService(("claude", "codex")),
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            compose = app.query_one("#compose", ComposeInput)
            compose.focus()
            compose.value = "/workspace beta"
            await pilot.press("enter")
            for _ in range(80):
                if app.workspace is not None and app.workspace.slug == "beta":
                    break
                await pilot.pause(0.02)
            await pilot.pause(0.05)
            assert app.workspace is not None and app.workspace.slug == "beta"
            assert app.sub_title == f"beta · {beta.project_root}"
            assert app.members == ("cursor",)
            listing = app.query_one("#members", ListView)
            assert [str(item.id) for item in listing.children] == [
                "conv-timeline",
                "member-cursor",
            ]
            text = events_text(app)
            assert "已经到 beta" in text
            assert "还在 alpha" not in text
            assert compose.members == ("cursor",)

    asyncio.run(scenario())


def test_workspace_is_a_first_class_command() -> None:
    assert "workspace" in COMMAND_NAMES
    runner = CommandRunner()
    assert "不可用" in runner.run("/workspace demo")[0]
    assert "用法:/workspace <名字>" in runner.run("/workspace")[0]
    help_text = "\n".join(runner.run("/help"))
    assert "/workspace" in help_text
