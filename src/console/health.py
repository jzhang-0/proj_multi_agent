"""总控台故障监控的兼容导出；实现位于 UI 无关的控制层。"""

from control.health import (
    Fault,
    FaultEvent,
    FaultKind,
    HealthMonitor,
    HealthTmux,
    WriteProbe,
    probe_writable,
)

ConsoleHealthMonitor = HealthMonitor

__all__ = [
    "ConsoleHealthMonitor",
    "Fault",
    "FaultEvent",
    "FaultKind",
    "HealthTmux",
    "WriteProbe",
    "probe_writable",
]
