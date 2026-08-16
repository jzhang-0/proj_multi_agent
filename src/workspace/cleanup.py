"""关掉一个工作区的成员会话,并回收已经没了登记的孤儿会话。"""

from __future__ import annotations

from workspace.limits import namespaced_sessions
from workspace.session import SessionNames, is_sessionless
from workspace.store import Store


def raw_tmux(tmux: object) -> object:
    """剥掉 NamespacedTmux,杀会话必须用真实名字。"""
    return getattr(tmux, "_inner", tmux)


def sessions_for_slug(tmux: object, slug: str) -> tuple[str, ...]:
    names = SessionNames(slug=slug)
    return tuple(session for session in namespaced_sessions(tmux) if names.owns(session))


def kill_sessions(tmux: object, sessions: tuple[str, ...]) -> list[str]:
    """按真实会话名杀掉;没有的忽略。返回实际发出 kill 的名字。"""
    client = raw_tmux(tmux)
    killed: list[str] = []
    for session in sessions:
        if is_sessionless(session):
            continue
        kill = getattr(client, "kill_session", None)
        if kill is None:
            continue
        kill(session, missing_ok=True)
        killed.append(session)
    return killed


def kill_workspace_sessions(tmux: object, slug: str) -> list[str]:
    """关掉 `<成员>@<slug>` 全部会话,不碰项目文件。"""
    return kill_sessions(tmux, sessions_for_slug(tmux, slug))


def orphan_sessions(tmux: object, store: Store) -> tuple[str, ...]:
    """状态目录已经没了、但 tmux 里还挂着的 `<成员>@<slug>`。"""
    known = store.slugs()
    orphans: list[str] = []
    for session in namespaced_sessions(tmux):
        _, sep, slug = session.rpartition("@")
        if sep and slug and slug not in known:
            orphans.append(session)
    return tuple(orphans)


def reclaim_orphans(tmux: object, store: Store) -> list[str]:
    """发现并杀掉孤儿会话。"""
    return kill_sessions(tmux, orphan_sessions(tmux, store))
