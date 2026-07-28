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
        )


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    occurred_at: datetime
    message: str


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    entries: tuple[ActivityEntry, ...]

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
class PortfolioSnapshot:
    equity: str
    realized_pnl: str
    unrealized_pnl: str
    current_drawdown: str
    total_return: str
    win_rate: str
    order_count: int
    position_count: int

    @classmethod
    def initial(cls) -> "PortfolioSnapshot":
        return cls(
            equity="$0.00",
            realized_pnl="$0.00",
            unrealized_pnl="$0.00",
            current_drawdown="0.00%",
            total_return="0.00%",
            win_rate="0.00%",
            order_count=0,
            position_count=0,
        )


@dataclass(frozen=True, slots=True)
class DecisionRow:
    symbol: str
    action: str
    confidence: str
    score: str
    rationale: str
    decided_at: str


@dataclass(frozen=True, slots=True)
class DecisionCenterSnapshot:
    cycle: str
    updated_at: str
    rows: tuple[DecisionRow, ...]

    @classmethod
    def initial(cls) -> "DecisionCenterSnapshot":
        return cls(
            cycle="Awaiting first cycle",
            updated_at="No decisions projected",
            rows=(),
        )


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    runtime: RuntimeSnapshot
    portfolio: PortfolioSnapshot
    activity: ActivitySnapshot
    positions: PositionsSnapshot
    orders: OrdersSnapshot
    decisions: DecisionCenterSnapshot

    @classmethod
    def initial(cls) -> "DashboardSnapshot":
        return cls(
            runtime=RuntimeSnapshot.initial(),
            portfolio=PortfolioSnapshot.initial(),
            activity=ActivitySnapshot.initial(),
            positions=PositionsSnapshot.initial(),
            orders=OrdersSnapshot.initial(),
            decisions=DecisionCenterSnapshot.initial(),
        )
