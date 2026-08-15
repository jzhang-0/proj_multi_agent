"""CON-010:硬指标测量脚本本身的判定逻辑。

实测数字由 `uv run python -m qa.perf` 现场跑(要真 tmux、真界面),这里只
钉住"预算怎么判"和"四条指标一条都不少"。
"""

import pytest

from console.app import MIRROR_INTERVAL
from qa.perf import Metric


def test_all_four_product_budgets_are_covered():
    from qa import perf

    source = perf.__doc__ or ""
    for budget in ("P95 < 200ms", "< 100ms", "单帧 < 16ms"):
        assert budget in source
    assert source.count("|") >= 20  # 四行指标表


@pytest.mark.parametrize(
    "samples,budget,ok",
    [
        ([10.0, 20.0, 30.0], 200, True),
        ([100.0] * 19 + [900.0], 200, False),  # P95 落在尾巴上,不许被中位数糊弄
    ],
)
def test_metric_judges_by_p95(samples, budget, ok):
    metric = Metric("测试项", budget, "p95", samples)
    assert metric.ok is ok
    assert "达标" in metric.line()


def test_skipped_metric_is_not_a_failure():
    metric = Metric("测试项", 100, "p95", [], skipped="没有 tmux")
    assert "跳过" in metric.line()
    assert not metric.ok


def test_mirror_interval_leaves_headroom_under_the_budget():
    # 定时器正好取 100ms 时,加上调度抖动实测 P95 会踩到 104ms
    assert MIRROR_INTERVAL < 0.1
