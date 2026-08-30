"""投递延迟实测:`uv run python -m bus.bench`。

量的是硬指标那一条:**消息入队 → send-keys 完成**。默认起一个临时 tmux
会话跑 `cat` 当收件人,发 N 条消息,统计每条从落盘到投递函数返回的耗时,
最后打印分布并按 P95 预算判定达标与否(退出码 0/1)。

用临时 bus 根目录和临时会话名,跑完全部清理,不碰仓库根 `bus/` 和真实成员。
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from bus.hub import DeliveryOutcome, DeliveryResult, Hub, format_line
from bus.message import Message
from bus.paths import BusPaths
from bus.queue import deposit

#: 产品定义的硬指标:入队 → 注入收件人终端 P95 < 200ms
P95_BUDGET_MS = 200.0


def percentile(values: Sequence[float], fraction: float) -> float:
    """取分位数(最近秩法),样本少时也不会炸。"""
    if not values:
        raise ValueError("没有样本")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


class BenchSession:
    """一个临时 tmux 会话,窗格里跑 `cat`,当收消息的假成员。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> BenchSession:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", self.name, "cat"],
            check=True,
            capture_output=True,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        subprocess.run(["tmux", "kill-session", "-t", self.name], capture_output=True)


def run_bench(
    count: int,
    interval: float,
    target: str,
    deliver: Callable[[Message], bool] | None = None,
) -> list[float]:
    """发 `count` 条消息,返回每条的入队 → 文本及 Enter 注入完成耗时(毫秒)。"""
    root = Path(tempfile.mkdtemp(prefix="bus-bench-"))
    try:
        if deliver is None:
            # 这条产品指标止于 send-keys 完成，不把随后针对成员 TUI 的
            # composer 确认时间混进来。临时会话名也是原样使用，不绑工作区。
            from tmuxctl import KeyInjector, Tmux

            injector = KeyInjector(Tmux())

            def inject_only(message: Message) -> bool:
                injector.deliver(message.to, format_line(message))
                return True

            deliver = inject_only

        paths = BusPaths.resolve(root).ensure()
        deposit_ns: dict[str, int] = {}
        latencies: list[float] = []
        failures: list[str] = []

        def on_result(result: DeliveryResult) -> None:
            done_ns = time.perf_counter_ns()
            message = result.message
            if message is None or message.id not in deposit_ns:
                return
            if result.outcome is not DeliveryOutcome.DELIVERED:
                failures.append(f"{result.outcome}: {result.detail}")
                return
            latencies.append((done_ns - deposit_ns[message.id]) / 1e6)

        stop = threading.Event()
        hub = Hub(paths, deliver=deliver, on_result=on_result, confirm=None)
        worker = threading.Thread(target=hub.run, kwargs={"stop": stop.is_set}, daemon=True)
        worker.start()

        for index in range(count):
            # 每条内容不同,免得撞上 BUS-002 的去重
            message = Message.create(target, f"bench {index} {uuid.uuid4().hex[:8]}", sender="bus")
            deposit_ns[message.id or ""] = time.perf_counter_ns()
            deposit(message, paths)
            time.sleep(interval)

        # 等结果的上限随样本数放大:机器上并发跑着几个 AI CLI 时,固定 10 秒
        # 会在还没测完的时候误判超时
        deadline = time.monotonic() + 10 + count * interval * 4
        while len(latencies) + len(failures) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        worker.join(timeout=2)

        if failures:
            raise RuntimeError(f"有 {len(failures)} 条没投出去,例如 {failures[0]}")
        if len(latencies) < count:
            raise RuntimeError(f"只收到 {len(latencies)}/{count} 条投递结果,超时")
        print(f"[bench] 投递循环模式:{hub.mode}")
        return latencies
    finally:
        shutil.rmtree(root, ignore_errors=True)


def report(latencies: list[float], budget_ms: float) -> bool:
    p95 = percentile(latencies, 0.95)
    print(
        f"[bench] 样本 {len(latencies)} 条  入队 → send-keys 完成(毫秒): "
        f"min {min(latencies):.1f}  P50 {statistics.median(latencies):.1f}  "
        f"P95 {p95:.1f}  max {max(latencies):.1f}"
    )
    ok = p95 < budget_ms
    print(f"[bench] P95 预算 {budget_ms:.0f}ms:{'达标' if ok else '不达标'}")
    return ok


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bus.bench", description="投递延迟实测")
    parser.add_argument("--count", type=int, default=30, help="发多少条消息(默认 30)")
    parser.add_argument("--interval", type=float, default=0.05, help="发送间隔秒数(默认 0.05)")
    parser.add_argument("--budget", type=float, default=P95_BUDGET_MS, help="P95 预算毫秒")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="不碰 tmux,只量总线自身的调度延迟(用于没有 tmux 的环境)",
    )
    args = parser.parse_args(argv)

    if args.fake:
        target = "fake-member"
        latencies = run_bench(args.count, args.interval, target, deliver=lambda m: bool(m.to))
        print("[bench] --fake:未经过 tmux,数字只包含总线调度部分")
        return 0 if report(latencies, args.budget) else 1

    if not tmux_available():
        print("[bench] 找不到 tmux;想只量总线部分请加 --fake")
        return 2

    name = f"bus-bench-{uuid.uuid4().hex[:6]}"
    with BenchSession(name):
        print(f"[bench] 收件人:临时会话 {name}(窗格里跑 cat)")
        print(f"[bench] 注入内容形如:{format_line(Message.create(name, 'bench 0', sender='bus'))}")
        latencies = run_bench(args.count, args.interval, name)
    return 0 if report(latencies, args.budget) else 1


if __name__ == "__main__":
    raise SystemExit(main())
