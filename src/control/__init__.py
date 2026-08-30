"""Web/TUI 共用控制面。

对外入口(WEB-002):

- `Lease`:文件锁 + 持有者 pid/心跳的单持有者租约原语,持有者崩溃或心跳
  超时后可被其他调用方自动回收,也支持显式抢占(`force=True`)。
- `HubDeliveryLease`:每工作区单一 Hub 投递租约,接入 `bus.Hub` /
  `console.buspump.BusPump` 的 `lease_gate`,非持有者只观察不投递。
- `MemberLeaseManager`:每成员单一交互租约(resize/直连/接管共用)。
- `leases_root`:租约文件的落盘位置约定。
"""

from control.lease import (
    DEFAULT_TTL_SECONDS,
    HubDeliveryLease,
    Lease,
    LeaseDenied,
    LeaseState,
    MemberLeaseManager,
    leases_root,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "HubDeliveryLease",
    "Lease",
    "LeaseDenied",
    "LeaseState",
    "MemberLeaseManager",
    "leases_root",
]
