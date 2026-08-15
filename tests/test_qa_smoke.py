"""QA-002 端到端冒烟。"""

from __future__ import annotations

from qa.smoke import run_smoke


def test_smoke_cat_pane_receives_enqueued_message() -> None:
    report = run_smoke()
    assert report.ok, report.detail
    assert report.latency_ms is not None
    assert report.latency_ms >= 0
    # 硬指标 P95 < 200ms 由 BUS-006 bench 钉死;这里只要求端到端真的跑通。
    # 单次采样给一个宽松上限,避免机器忙时把冒烟变成 flake。
    assert report.latency_ms < 3000
