"""WS-002:成员名 ↔ tmux 会话名收口到 workspace.session。"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from roster.lifecycle import Lifecycle
from roster.schema import Member, Roster, RosterError, validate_member_name
from tmuxctl import Tmux
from workspace.session import (
    NamespacedTmux,
    SessionNameError,
    SessionNames,
    bind_tmux,
    is_sessionless,
    member_for,
    session_for,
)
from workspace.store import Store


def test_session_of_uses_member_at_slug() -> None:
    names = SessionNames("demo")
    assert names.session_of("claude") == "claude@demo"
    assert names.member_of("claude@demo") == "claude"
    assert names.owns("claude@demo") is True
    assert names.owns("claude@other") is False
    assert names.session_of("claude@demo") == "claude@demo"  # 不二次拼接


def test_identity_mapping_keeps_short_names() -> None:
    names = SessionNames.identity()
    assert names.session_of("claude") == "claude"
    assert names.member_of("claude") == "claude"
    assert names.is_identity is True


def test_reserved_names_do_not_get_slug() -> None:
    names = SessionNames("demo")
    for reserved in ("human", "bus", "im:小明"):
        assert is_sessionless(reserved)
        with pytest.raises(SessionNameError, match="没有 tmux 会话"):
            names.session_of(reserved)


def test_colon_and_dot_rejected_on_member_side() -> None:
    names = SessionNames("demo")
    with pytest.raises(SessionNameError, match="session:window"):
        names.session_of("foo:bar")
    with pytest.raises(SessionNameError, match="静默改写成"):
        names.session_of("foo.bar")
    with pytest.raises(RosterError, match="session:window"):
        validate_member_name("a:b")
    with pytest.raises(RosterError, match="静默改写成"):
        validate_member_name("a.b")
    with pytest.raises(RosterError, match="不能含 '@'"):
        validate_member_name("a@b")


def test_from_cwd_uses_registered_workspace(tmp_path: Path) -> None:
    store = Store(tmp_path / "home")
    project = tmp_path / "demo"
    project.mkdir()
    store.add(project, slug="demo")
    names = SessionNames.from_cwd(project / "src", store=store)
    assert names.slug == "demo"
    assert SessionNames.from_cwd(tmp_path, store=store).is_identity


def test_session_for_reads_bound_tmux() -> None:
    names = SessionNames("ws")
    holder = type("T", (), {"session_names": names})()
    assert session_for(holder, "agy") == "agy@ws"
    assert member_for(holder, "agy@ws") == "agy"
    assert session_for(object(), "agy") == "agy"


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_tmux() -> Iterator[Tmux]:
    if shutil.which("tmux") is None:
        pytest.skip("没有 tmux")
    socket = f"ws002-{uuid.uuid4().hex[:8]}"
    client = Tmux(socket_name=socket, timeout=5.0)
    try:
        yield client
    finally:
        client.kill_server()


def test_bound_tmux_creates_namespaced_session(isolated_tmux: Tmux) -> None:
    names = SessionNames("demo")
    bound = bind_tmux(isolated_tmux, names)
    assert isinstance(bound, NamespacedTmux)
    member = Member(name="bot", command="cat")
    life = Lifecycle(Roster(members=(member,)), bound)
    try:
        result = life.up("bot")[0]
        assert result.changed
        assert "tmux attach -t bot@demo" in result.detail
        assert bound.has_session("bot") is True
        assert isolated_tmux.has_session("bot@demo") is True
        assert isolated_tmux.has_session("bot") is False
        panes = bound.list_panes("bot")
        assert panes[0].session_name == "bot@demo"
    finally:
        life.down("bot")
    assert isolated_tmux.has_session("bot@demo") is False


def test_pane_id_is_not_remapped(isolated_tmux: Tmux) -> None:
    names = SessionNames("demo")
    bound = bind_tmux(isolated_tmux, names)
    bound.new_session("bot", command=["cat"])
    try:
        pane = bound.list_panes("bot")[0]
        text = bound.capture_pane(pane.pane_id)
        assert isinstance(text, str)
    finally:
        bound.kill_session("bot", missing_ok=True)


def test_production_constructors_go_through_bind_tmux() -> None:
    """32 处收口:生产路径不许再直接 Tmux() 对着成员会话。"""
    files = [
        ROOT / "src/bus/hub.py",
        ROOT / "src/roster/__main__.py",
        ROOT / "src/console/app.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "bind_tmux" in text, f"{path} 没有走 bind_tmux"
        assert "Tmux()" not in text, f"{path} 仍在直接构造 Tmux()"
