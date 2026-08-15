"""CON-001:应用骨架起得来、退得干净、投递循环内嵌在里面转。

界面本身的样子按视觉自验证流程另外看图确认(证据在 Goal 里),这里只
测行为。全部用临时 bus 根目录和假投递函数,不碰真实成员会话。
"""

import asyncio

import pytest

from bus import Message, deposit, pending
from bus.paths import BusPaths
from console.app import ConsoleApp
from console.cli import main as console_main


@pytest.fixture
def paths(tmp_path):
    return BusPaths.resolve(tmp_path / "bus").ensure()


def run_async(coro_factory):
    return asyncio.run(coro_factory())


def app_with(paths, sink):
    def deliver(message):
        sink.append(message.text)
        return True

    return ConsoleApp(paths, deliver=deliver)


def events_text(app):
    """把时间线上已渲染的文本抠出来(去掉样式,只留字)。"""
    from console.widgets import Timeline

    log = app.query_one("#timeline", Timeline)
    return "\n".join("".join(segment.text for segment in line) for line in log.lines)


def test_app_starts_pumps_the_bus_and_shows_the_traffic(paths):
    delivered: list[str] = []
    app = app_with(paths, delivered)

    async def scenario():
        async with app.run_test() as pilot:
            assert app.pump.is_running()
            deposit(Message.create("codex", "内嵌循环收到没", sender="claude"), paths)
            for _ in range(200):
                if delivered:
                    break
                await pilot.pause(0.02)
            await pilot.pause(0.05)
            assert delivered == ["内嵌循环收到没"]
            assert pending(paths) == []
            assert "claude → codex: 内嵌循环收到没" in events_text(app)

    run_async(scenario)


def test_q_quits_and_stops_the_pump(paths):
    app = app_with(paths, [])

    async def scenario():
        async with app.run_test() as pilot:
            assert app.pump.is_running()
            await pilot.press("q")
            await pilot.pause(0.05)

    run_async(scenario)
    assert not app.pump.is_running()  # 线程收干净了,没有留下野线程


def test_quitting_never_touches_tmux(paths, monkeypatch):
    """退出路径只停循环、关应用;任何 tmux 命令都不该被调到。"""
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError(f"总控台退出时不该执行外部命令: {args}")

    app = app_with(paths, [])

    async def scenario():
        async with app.run_test() as pilot:
            monkeypatch.setattr(subprocess, "run", forbidden)
            await pilot.press("q")
            await pilot.pause(0.05)

    run_async(scenario)


def test_headless_is_the_plain_hub(paths, capsys):
    deposit(Message.create("human", "无界面模式也要上屏", sender="claude"), paths)
    code = console_main(["--headless", "--bus-root", str(paths.root), "--once"])
    assert code == 0
    assert "claude -> human: 无界面模式也要上屏" in capsys.readouterr().out
    assert pending(paths) == []


def test_once_without_headless_is_rejected(paths, capsys):
    assert console_main(["--once", "--bus-root", str(paths.root)]) == 1
    assert "只在 --headless 下有意义" in capsys.readouterr().out
