from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .runtime import RuntimeState


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    environment: str
    state: RuntimeState
    broker_status: str
    market_feed_status: str
    inference_status: str
    emergency_stop_enabled: bool
    active_model: str
    cycle_count: int
    status_message: str
    account: str = "--"
    runtime_duration: str = "--"

    @classmethod
    def initial(cls) -> "RuntimeSnapshot":
        return cls(
            environment="PAPER",
            state=RuntimeState.STOPPED,
            broker_status="Disconnected",
            market_feed_status="Idle",
            inference_status="Ready",
            emergency_stop_enabled=True,
            active_model="Not loaded",
            cycle_count=0,
            status_message="Ready to start.",
            account="--",
            runtime_duration="--",
        )


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    occurred_at: datetime
    message: str
    category: str = "SYSTEM"
    severity: str = "INFO"
    source: str = "operations"
    related_symbol: str | None = None
    related_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineFilter:
    severity: str = "ALL"
    category: str = "ALL"
    symbol: str = "ALL"
    search: str = ""


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    entries: tuple[ActivityEntry, ...]
    filters: TimelineFilter = TimelineFilter()
    severity_options: tuple[str, ...] = ("ALL",)
    category_options: tuple[str, ...] = ("ALL",)
    symbol_options: tuple[str, ...] = ("ALL",)

    @classmethod
    def initial(cls) -> "ActivitySnapshot":
        return cls(entries=())


@dataclass(frozen=True, slots=True)
class PositionsSnapshot:
    rows: tuple[tuple[str, str, str, str], ...]

    @classmethod
    def initial(cls) -> "PositionsSnapshot":
        return cls(rows=())


@dataclass(frozen=True, slots=True)
class OrdersSnapshot:
    rows: tuple[tuple[str, str, str], ...]

    @classmethod
    def initial(cls) -> "OrdersSnapshot":
        return cls(rows=())


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    runtime: RuntimeSnapshot
    activity: ActivitySnapshot
    positions: PositionsSnapshot
    orders: OrdersSnapshot

    @classmethod
    def initial(cls) -> "DashboardSnapshot":
        return cls(
            runtime=RuntimeSnapshot.initial(),
            activity=ActivitySnapshot.initial(),
            positions=PositionsSnapshot.initial(),
            orders=OrdersSnapshot.initial(),
        )
