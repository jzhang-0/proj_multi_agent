"""WEB-002:租约原语、Hub 投递租约接入、每成员交互租约。"""

import os

import pytest

from bus import BusPaths, DeliveryOutcome, Hub, Message, deposit, pending
from control.lease import (
    HubDeliveryLease,
    Lease,
    LeaseDenied,
    MemberLeaseManager,
    leases_root,
)
from workspace.model import Workspace

SELF_PID = os.getpid()
GHOST_PID = 999999999  # 大概率不存在的 pid,用来模拟崩溃的持有者


# --- Lease 原语 --------------------------------------------------------------


def test_acquire_then_release_roundtrip(tmp_path):
    lease = Lease(tmp_path / "hub.json")

    state = lease.acquire("front-a")

    assert state.owner == "front-a"
    assert lease.holds("front-a")
    lease.release("front-a")
    assert lease.current() is None


def test_same_owner_renews_without_being_treated_as_new_holder(tmp_path):
    lease = Lease(tmp_path / "hub.json")

    first = lease.acquire("front-a", now=100.0)
    second = lease.acquire("front-a", now=101.0)

    assert second.acquired_at == first.acquired_at
    assert second.heartbeat_at == 101.0


def test_live_holder_denies_other_owner_without_force(tmp_path):
    lease = Lease(tmp_path / "hub.json", ttl=5.0)
    lease.acquire("front-a", pid=SELF_PID, now=1000.0)

    with pytest.raises(LeaseDenied) as excinfo:
        lease.acquire("front-b", now=1001.0)

    assert excinfo.value.holder.owner == "front-a"
    assert lease.holds("front-a")


def test_force_preempts_a_live_holder(tmp_path):
    lease = Lease(tmp_path / "hub.json", ttl=5.0)
    lease.acquire("front-a", pid=SELF_PID, now=1000.0)

    lease.acquire("front-b", now=1001.0, force=True)

    assert lease.holds("front-b")
    assert not lease.holds("front-a")


def test_dead_pid_is_reclaimed_even_within_ttl(tmp_path):
    lease = Lease(tmp_path / "hub.json", ttl=15.0)
    lease.acquire("crashed", pid=GHOST_PID, now=1000.0)

    # 心跳只过了半秒,远没到 ttl,但持有者的进程已经不在了。
    reclaimed = lease.acquire("front-b", now=1000.5)

    assert reclaimed.owner == "front-b"


def test_stale_heartbeat_is_reclaimed_even_with_live_pid(tmp_path):
    lease = Lease(tmp_path / "hub.json", ttl=5.0)
    lease.acquire("front-a", pid=SELF_PID, now=1000.0)

    # 进程还活着(就是当前测试进程),但心跳已经过期。
    reclaimed = lease.acquire("front-b", now=1010.0)

    assert reclaimed.owner == "front-b"


def test_heartbeat_returns_false_once_lease_no_longer_mine(tmp_path):
    lease = Lease(tmp_path / "hub.json", ttl=5.0)
    lease.acquire("front-a", now=1000.0)
    lease.acquire("front-b", now=1001.0, force=True)

    assert lease.heartbeat("front-a", now=1002.0) is False


def test_release_by_non_holder_is_noop(tmp_path):
    lease = Lease(tmp_path / "hub.json")
    lease.acquire("front-a")

    lease.release("front-b")

    assert lease.holds("front-a")


# --- HubDeliveryLease:每工作区单一 Hub 投递租约 ------------------------------


def test_hub_delivery_lease_only_one_side_delivers_until_release(tmp_path):
    path = tmp_path / "hub-delivery.json"
    primary = HubDeliveryLease(path, "front-a", ttl=5.0)
    standby = HubDeliveryLease(path, "front-b", ttl=5.0)

    assert primary.should_deliver() is True
    assert primary.held is True
    assert standby.should_deliver() is False
    assert standby.held is False

    primary.release()

    assert standby.should_deliver() is True
    assert standby.held is True


def test_hub_gated_by_lease_only_observes_when_denied(tmp_path):
    paths = BusPaths.resolve(tmp_path / "bus").ensure()
    deposit(Message.create("codex", "hello", sender="claude"), paths)

    observer = Hub(paths, deliver=lambda message: True, lease_gate=lambda: False)
    assert observer.drain_once() == []
    assert len(pending(paths)) == 1  # 只观察:队列文件原样留着

    holder = Hub(paths, deliver=lambda message: True, lease_gate=lambda: True)
    results = holder.drain_once()

    assert [result.outcome for result in results] == [DeliveryOutcome.DELIVERED]
    assert pending(paths) == []


# --- MemberLeaseManager:每成员交互租约 ---------------------------------------


def test_member_lease_denies_other_owner_then_allows_explicit_preemption(tmp_path):
    manager = MemberLeaseManager(tmp_path / "leases", ttl=5.0)
    manager.acquire("codex", "tui-1", force=False)

    with pytest.raises(LeaseDenied):
        manager.acquire("codex", "tui-2")

    manager.acquire("codex", "tui-2", force=True)

    assert manager.holds("codex", "tui-2")
    assert not manager.holds("codex", "tui-1")


def test_member_leases_are_independent_per_member(tmp_path):
    manager = MemberLeaseManager(tmp_path / "leases", ttl=5.0)
    manager.acquire("codex", "tui-2", force=True)

    manager.acquire("claude", "tui-1")

    assert manager.holds("codex", "tui-2")
    assert manager.holds("claude", "tui-1")


def test_member_lease_release_frees_the_slot(tmp_path):
    manager = MemberLeaseManager(tmp_path / "leases", ttl=5.0)
    manager.acquire("codex", "tui-1")

    manager.release("codex", "tui-1")

    assert manager.holder("codex") is None


def test_leases_root_is_under_workspace_control_state(tmp_path):
    workspace = Workspace(slug="demo", project_root=tmp_path, state_dir=tmp_path / "state")

    assert leases_root(workspace) == tmp_path / "state" / "control" / "leases"
