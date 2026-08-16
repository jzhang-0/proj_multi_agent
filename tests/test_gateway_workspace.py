"""WS-008:网关按工作区路由、按区白名单,GATE-004 在多区下仍生效。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bus import pending, read_message
from bus.paths import BusPaths
from bus.policy import OutboundPolicy
from gateway.base import Gateway, GroupMessage
from gateway.config import GatewayConfig, WorkspaceGate
from gateway.security import PendingStore, SecurityPolicy
from gateway.workspaces import WorkspaceBinder
from workspace.paths import ENV_AMUX_HOME
from workspace.store import Store

MEMBERS = ("claude", "codex", "cursor")


class FakeAdapter:
    def __init__(self) -> None:
        self.posts = []

    async def start(self, on_message) -> None:
        self.on_message = on_message

    async def stop(self) -> None:
        return None

    async def post(self, post) -> None:
        self.posts.append(post)


def queued(paths: BusPaths):
    return [read_message(path) for path in pending(paths)]


def make_project(root: Path, name: str, enabled: list[str]) -> Path:
    project = root / "projects" / name
    project.mkdir(parents=True)
    listed = ", ".join(f'"{item}"' for item in enabled)
    (project / "amux.toml").write_text(f"enabled = [{listed}]\n", encoding="utf-8")
    return project


@pytest.fixture
def pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_AMUX_HOME, str(tmp_path / "amux-home"))
    store = Store.default()
    alpha_root = make_project(tmp_path, "alpha", ["claude", "codex"])
    beta_root = make_project(tmp_path, "beta", ["cursor"])
    alpha, _ = store.add(alpha_root, slug="alpha")
    beta, _ = store.add(beta_root, slug="beta")
    fallback = BusPaths.resolve(tmp_path / "fallback-bus").ensure()
    config = GatewayConfig(
        users=("小明", "小红"),
        rooms=("alpha", "beta"),
        workspaces=(
            WorkspaceGate(slug="alpha", users=("小明",), rooms=("alpha",)),
            WorkspaceGate(slug="beta", users=("小红",), rooms=("beta",)),
        ),
    )
    security = SecurityPolicy.from_config(config.rooms, config.users, "default")
    binder = WorkspaceBinder(
        store=store,
        config=config,
        fallback=fallback,
        fallback_security=security,
        fallback_members=lambda: MEMBERS,
    )
    gateway = Gateway(
        FakeAdapter(),
        fallback,
        members=lambda: MEMBERS,
        security=security,
        binder=binder,
    )
    return gateway, alpha, beta, fallback


def test_config_loads_per_workspace_whitelist(tmp_path: Path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(
        'users = ["全局"]\nrooms = ["default"]\n\n'
        '[workspaces.alpha]\nusers = ["小明"]\nrooms = ["alpha"]\n',
        encoding="utf-8",
    )
    config = GatewayConfig.load(path)
    spec = config.workspace_spec("alpha")
    assert spec is not None
    assert spec.users == ("小明",)
    assert spec.rooms == ("alpha",)


def test_messages_go_to_the_workspace_named_by_the_room(pair) -> None:
    gateway, alpha, beta, fallback = pair
    ok = gateway.on_group_message(GroupMessage("小明", "@claude 只给 alpha", room="alpha"))
    assert ok.error == ""
    alpha_bus = BusPaths.for_workspace(alpha)
    beta_bus = BusPaths.for_workspace(beta)
    sent = queued(alpha_bus)
    assert [(m.to, m.text, m.sender, m.extra.get("workspace")) for m in sent] == [
        ("claude", "只给 alpha", "im:小明", "alpha")
    ]
    assert queued(beta_bus) == []
    assert queued(fallback) == []


def test_two_workspaces_do_not_share_a_queue(pair) -> None:
    gateway, alpha, beta, _fallback = pair
    gateway.on_group_message(GroupMessage("小明", "@claude alpha 活", room="alpha"))
    gateway.on_group_message(GroupMessage("小红", "@cursor beta 活", room="beta"))
    assert [m.text for m in queued(BusPaths.for_workspace(alpha))] == ["alpha 活"]
    assert [m.text for m in queued(BusPaths.for_workspace(beta))] == ["beta 活"]


def test_whitelist_is_per_workspace(pair) -> None:
    gateway, alpha, beta, _fallback = pair
    refused = gateway.on_group_message(GroupMessage("小明", "@cursor 串台", room="beta"))
    assert "不在白名单里" in refused.error
    assert queued(BusPaths.for_workspace(beta)) == []
    assert queued(BusPaths.for_workspace(alpha)) == []


def test_remote_mark_sanitize_rate_limit_still_apply(pair) -> None:
    gateway, alpha, _beta, _fallback = pair
    paths = BusPaths.for_workspace(alpha)
    gateway.on_group_message(
        GroupMessage("小明", "@claude \x1b]0;假标题\x07第一句", room="alpha")
    )
    for index in range(8):
        gateway.on_group_message(GroupMessage("小明", f"@claude 第 {index} 条", room="alpha"))
    sent = queued(paths)
    assert sent[0].text == "第一句"
    assert {m.sender for m in sent} == {"im:小明"}
    policy = OutboundPolicy()
    verdicts = []
    for message in sent:
        verdict = policy.check(message)
        verdicts.append(verdict.ok)
        if verdict.ok:
            policy.record(message)
    assert verdicts[-1] is False
    assert "发送过于频繁" in policy.check(sent[-1]).reason


def test_dangerous_remote_instruction_holds_on_that_workspace(pair) -> None:
    gateway, alpha, beta, _fallback = pair
    route = gateway.on_group_message(GroupMessage("小明", "@claude git push 一下", room="alpha"))
    assert "本机确认" in route.error
    alpha_bus = BusPaths.for_workspace(alpha)
    assert [m.to for m in queued(alpha_bus)] == ["human"]
    assert queued(BusPaths.for_workspace(beta)) == []
    items = PendingStore(alpha_bus).entries()
    assert len(items) == 1
    assert items[0]["user"] == "小明"
    assert items[0]["room"] == "alpha"


def test_claiming_human_still_cannot_bypass_confirmation(pair) -> None:
    """手机上的人自称 human 也没用:远程指令天然弱于本机指令。"""
    from dataclasses import replace

    gateway, alpha, _beta, _fallback = pair
    gateway.binder.config = replace(
        gateway.binder.config,
        workspaces=(WorkspaceGate(slug="alpha", users=("human",), rooms=("alpha",)),),
    )
    route = gateway.on_group_message(GroupMessage("human", "@claude git push", room="alpha"))
    assert "本机确认" in route.error
    assert [m.to for m in queued(BusPaths.for_workspace(alpha))] == ["human"]
    assert PendingStore(BusPaths.for_workspace(alpha)).entries()[0]["user"] == "human"


def test_local_approval_releases_into_the_same_workspace(pair) -> None:
    gateway, alpha, _beta, _fallback = pair
    gateway.on_group_message(GroupMessage("小明", "@claude git push 一下", room="alpha"))
    paths = BusPaths.for_workspace(alpha)
    request_id = str(PendingStore(paths).entries()[0]["id"])
    assert PendingStore(paths).approve(request_id)
    asyncio.run(gateway.pump_once())
    released = [m for m in queued(paths) if m.to == "claude"]
    assert [m.text for m in released] == ["git push 一下"]
    assert [m.sender for m in released] == ["im:小明"]
    assert [m.extra.get("workspace") for m in released] == ["alpha"]
