"""硬指标实测:`uv run python -m qa.perf`。

产品定义里的四条延迟预算,一条命令全量过一遍,打印实测分布并按预算判定:

| 指标 | 预算 | 怎么测 |
|---|---|---|
| 消息入队 → 注入收件人终端 | P95 < 200ms | 临时 tmux 会话跑 `cat`,复用 `bus.bench` |
| 消息 → 时间线上屏 | < 200ms | 起 console(Textual 测试驱动),从入队计时到那行出现在时间线 |
| 成员详情画面刷新 | < 100ms | 真开一个不停输出的窗格,量镜像相邻两次更新的间隔 |
| 键入回显 | 单帧 < 16ms | 量一次按键让应用消耗的 CPU 时间(墙上时间里绝大部分是等空闲) |

全部用临时 bus 根目录和临时 tmux 会话,不碰仓库根 `bus/` 和真实成员。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import statistics
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from bus.bench import percentile, run_bench
from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit
from console.app import MIRROR_INTERVAL, ConsoleApp
from console.mirror import Mirror
from console.widgets import Timeline

#: 每项指标采多少样本。P95 要有意义,样本别太少(20 个样本的 P95 就是最大值)
SAMPLES = 50

#: 投递测量的发送间隔(秒)。比单条投递耗时大,量的才是延迟而不是排队
DELIVERY_INTERVAL = 0.2


@dataclass
class Metric:
    """一条指标的实测结果。"""

    name: str
    budget_ms: float
    statistic: str
    samples: list[float] = field(default_factory=list)
    skipped: str = ""

    @property
    def value(self) -> float:
        if self.statistic == "p95":
            return percentile(self.samples, 0.95)
        return max(self.samples)

    @property
    def ok(self) -> bool:
        return bool(self.samples) and self.value < self.budget_ms

    def line(self) -> str:
        if self.skipped:
            return f"  {self.name:<24} 跳过:{self.skipped}"
        return (
            f"  {self.name:<24} {self.statistic.upper()} {self.value:6.1f}ms "
            f"(预算 {self.budget_ms:.0f}ms,样本 {len(self.samples)},"
            f"min {min(self.samples):.1f} / 中位 {statistics.median(self.samples):.1f} / "
            f"max {max(self.samples):.1f})  {'达标' if self.ok else '不达标'}"
        )


def timeline_lines(app: ConsoleApp) -> list[str]:
    log = app.query_one("#timeline", Timeline)
    return ["".join(segment.text for segment in line) for line in log.lines]


async def measure_timeline_and_keystrokes(samples: int) -> tuple[list[float], list[float]]:
    """一次起停里把「上屏延迟」和「键入回显」都量了。"""
    root = Path(tempfile.mkdtemp(prefix="qa-perf-"))
    try:
        paths = BusPaths.resolve(root).ensure()
        app = ConsoleApp(paths, deliver=lambda message: True, members=("claude", "codex"))
        onscreen: list[float] = []
        keystrokes: list[float] = []
        async with app.run_test(size=(120, 30)) as pilot:
            for index in range(samples):
                token = f"perf-{index}-{uuid.uuid4().hex[:6]}"
                started = time.perf_counter()
                deposit(Message.create("codex", token, sender="human"), paths)
                while not any(token in line for line in timeline_lines(app)):
                    await pilot.pause(0.001)
                onscreen.append((time.perf_counter() - started) * 1000)

            app.query_one("#compose").focus()
            await pilot.pause()
            for index in range(samples):
                # 量 CPU 时间而不是墙上时间:`press()` 的往返里绝大部分是等
                # 事件循环空闲(裸 Textual 应用同样要 60ms+),那是驱动器的
                # 开销不是帧的开销。帧预算关心的是"这一键让应用干了多少活"。
                started = time.process_time()
                await pilot.press("x" if index % 2 else "backspace")
                keystrokes.append((time.process_time() - started) * 1000)
        return onscreen, keystrokes
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def measure_mirror(samples: int) -> tuple[list[float], str]:
    """量详情栏镜像相邻两次更新的间隔(窗格一直在输出,属于活跃窗格)。"""
    if shutil.which("tmux") is None:
        return [], "没有 tmux"
    from tmuxctl import PaneSnapshotter, Tmux

    session = f"perf-{uuid.uuid4().hex[:6]}"
    root = Path(tempfile.mkdtemp(prefix="qa-perf-"))
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session,
            "sh -c 'while true; do date +%s%N; sleep 0.02; done'",
        ],
        check=True,
        capture_output=True,
    )
    try:
        paths = BusPaths.resolve(root).ensure()
        app = ConsoleApp(
            paths,
            deliver=lambda message: True,
            members=(session,),
            snapshotter=PaneSnapshotter(Tmux(), min_interval=MIRROR_INTERVAL / 2),
        )
        intervals: list[float] = []
        async with app.run_test(size=(120, 30)) as pilot:
            app.select_member(session)
            mirror = app.query_one("#detail", Mirror)
            previous_text, previous_at = mirror.screen_text, None
            deadline = time.perf_counter() + 10
            while len(intervals) < samples and time.perf_counter() < deadline:
                await pilot.pause(0.002)
                if mirror.screen_text == previous_text:
                    continue
                now = time.perf_counter()
                if previous_at is not None:
                    intervals.append((now - previous_at) * 1000)
                previous_text, previous_at = mirror.screen_text, now
        return intervals, "" if intervals else "窗格没有产生可观测的更新"
    finally:
        subprocess.run(["tmux", "kill-session", "-t", f"={session}"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def measure_delivery(samples: int) -> tuple[list[float], str]:
    """入队 → send-keys 完成,直接复用 BUS-006 的 bench。"""
    if shutil.which("tmux") is None:
        return [], "没有 tmux"
    from bus.bench import BenchSession

    session = f"perf-bench-{uuid.uuid4().hex[:6]}"
    with BenchSession(session):
        return run_bench(samples, DELIVERY_INTERVAL, session), ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qa.perf", description="总控台硬指标实测")
    parser.add_argument("--samples", type=int, default=SAMPLES, help=f"每项样本数(默认 {SAMPLES})")
    args = parser.parse_args(argv)

    print(f"[perf] 每项 {args.samples} 个样本,临时目录 + 临时会话,不碰真实成员\n")

    delivery = Metric("入队 → 注入终端", 200, "p95")
    delivery.samples, delivery.skipped = measure_delivery(args.samples)

    onscreen = Metric("消息 → 时间线上屏", 200, "p95")
    keystroke = Metric("键入回显 CPU(单帧)", 16, "p95")
    onscreen.samples, keystroke.samples = asyncio.run(
        measure_timeline_and_keystrokes(args.samples)
    )

    mirror = Metric("详情画面刷新间隔", 100, "p95")
    mirror.samples, mirror.skipped = asyncio.run(measure_mirror(args.samples))

    metrics = [delivery, onscreen, mirror, keystroke]
    print("[perf] 结果:")
    for metric in metrics:
        print(metric.line())

    failed = [metric for metric in metrics if not metric.skipped and not metric.ok]
    print()
    if failed:
        print(f"[perf] 不达标:{', '.join(metric.name for metric in failed)}")
        return 1
    print("[perf] 四项预算全部达标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
