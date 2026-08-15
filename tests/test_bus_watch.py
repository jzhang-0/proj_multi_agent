"""BUS-006:watchfiles 低延迟投递与轮询回退。

不碰 tmux(投递函数注入成假的),只验证"新消息落盘 → 投递循环醒过来"。
"""

import threading
import time

import pytest

from bus import Hub, Message, deposit, pending
from bus.hub import watchfiles_available
from bus.paths import BusPaths

#: 单条消息从落盘到被投递的等待上限(秒),远松于 200ms 预算,避免机器忙时假失败
WAKE_DEADLINE = 3.0


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def run_hub_in_background(hub):
    stop = threading.Event()
    worker = threading.Thread(target=hub.run, kwargs={"stop": stop.is_set}, daemon=True)
    worker.start()
    return stop, worker


def wait_until(predicate, deadline=WAKE_DEADLINE):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def deliver_into(sink):
    def deliver(message):
        sink.append(message.text)
        return True

    return deliver


def test_watchfiles_is_available_in_this_project():
    # 装不上时会静默退回轮询,所以这里明确钉一下:本项目按架构决策要用 watchfiles
    assert watchfiles_available()


def test_watch_mode_picks_up_new_message(paths):
    got = []
    hub = Hub(paths, deliver=deliver_into(got))
    stop, worker = run_hub_in_background(hub)
    try:
        assert wait_until(lambda: hub.mode == "watch")
        deposit(Message.create("codex", "监听模式收到没", sender="claude"), paths)
        assert wait_until(lambda: got == ["监听模式收到没"])
        assert pending(paths) == []
    finally:
        stop.set()
        worker.join(timeout=2)


def test_falls_back_to_polling_when_watchfiles_missing(paths, monkeypatch):
    monkeypatch.setattr("bus.hub.watchfiles_available", lambda: False)
    got = []
    hub = Hub(paths, deliver=deliver_into(got), poll_interval=0.05)
    stop, worker = run_hub_in_background(hub)
    try:
        assert wait_until(lambda: hub.mode == "poll")
        deposit(Message.create("codex", "轮询模式收到没", sender="claude"), paths)
        assert wait_until(lambda: got == ["轮询模式收到没"])
    finally:
        stop.set()
        worker.join(timeout=2)


def test_backlog_deposited_before_start_is_drained(paths):
    deposit(Message.create("codex", "开循环之前就在队列里", sender="claude"), paths)
    got = []
    hub = Hub(paths, deliver=deliver_into(got))
    stop, worker = run_hub_in_background(hub)
    try:
        assert wait_until(lambda: got == ["开循环之前就在队列里"])
    finally:
        stop.set()
        worker.join(timeout=2)
