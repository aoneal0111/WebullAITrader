from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.read_models.operator_workspace import OperatorWorkspaceSnapshot
from app.replay import ReplaySnapshot
from app.recording import RecordingSnapshot
from app.event_store import EventStoreSnapshot
from app.analytics import AnalyticsSnapshot
from app.backtesting.models import ExperimentSnapshot

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
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None

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
            started_at=None,
            last_heartbeat_at=None,
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
    symbols: tuple[str, ...] = ()
    selected_symbol: str | None = None

    @classmethod
    def initial(cls) -> "PositionsSnapshot":
        return cls(rows=())


@dataclass(frozen=True, slots=True)
class OrdersSnapshot:
    rows: tuple[tuple[str, str, str], ...]
    symbols: tuple[str, ...] = ()
    order_ids: tuple[str, ...] = ()
    selected_order: str | None = None

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
    selected_symbol: str | None = None

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
    selection_id: str = ""


@dataclass(frozen=True, slots=True)
class DecisionCenterSnapshot:
    cycle: str
    updated_at: str
    rows: tuple[DecisionRow, ...]
    selected_decision: str | None = None

    @classmethod
    def initial(cls) -> "DecisionCenterSnapshot":
        return cls(
            cycle="Awaiting first cycle",
            updated_at="No decisions projected",
            rows=(),
        )


@dataclass(frozen=True, slots=True)
class HealthBadgeSnapshot:
    label: str
    value: str
    level: str


@dataclass(frozen=True, slots=True)
class HealthCenterSnapshot:
    overall_health: HealthBadgeSnapshot
    runtime_state: HealthBadgeSnapshot
    broker_status: HealthBadgeSnapshot
    scanner_status: HealthBadgeSnapshot
    market_data_status: HealthBadgeSnapshot
    operations_bus_status: HealthBadgeSnapshot
    current_cycle: HealthBadgeSnapshot
    last_completed_cycle: HealthBadgeSnapshot
    last_update_time: HealthBadgeSnapshot
    warnings: tuple[HealthBadgeSnapshot, ...]
    errors: tuple[HealthBadgeSnapshot, ...]

    @classmethod
    def initial(cls) -> "HealthCenterSnapshot":
        return cls(
            overall_health=HealthBadgeSnapshot(
                "Overall Health",
                "UNKNOWN",
                "neutral",
            ),
            runtime_state=HealthBadgeSnapshot(
                "Runtime State",
                "STOPPED",
                "neutral",
            ),
            broker_status=HealthBadgeSnapshot(
                "Broker Status",
                "Unknown",
                "neutral",
            ),
            scanner_status=HealthBadgeSnapshot(
                "Scanner Status",
                "Unknown",
                "neutral",
            ),
            market_data_status=HealthBadgeSnapshot(
                "Market Data Status",
                "Unknown",
                "neutral",
            ),
            operations_bus_status=HealthBadgeSnapshot(
                "Operations Bus Status",
                "Unknown",
                "neutral",
            ),
            current_cycle=HealthBadgeSnapshot(
                "Current Cycle",
                "0",
                "neutral",
            ),
            last_completed_cycle=HealthBadgeSnapshot(
                "Last Completed Cycle",
                "0",
                "neutral",
            ),
            last_update_time=HealthBadgeSnapshot(
                "Last Update Time",
                "NEVER",
                "neutral",
            ),
            warnings=(),
            errors=(),
        )


@dataclass(frozen=True, slots=True)
class TimelineRow:
    time: str
    category: str
    severity: str
    summary: str
    selection_id: str = ""
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    rows: tuple[TimelineRow, ...]
    max_entries: int
    selected_entry: str | None = None

    @classmethod
    def initial(cls) -> "TimelineSnapshot":
        return cls(rows=(), max_entries=500)


@dataclass(frozen=True, slots=True)
class LifecycleEntryRow:
    time: str
    phase: str
    summary: str


@dataclass(frozen=True, slots=True)
class LifecycleRow:
    symbol: str
    status: str
    opened: str
    closed: str
    realized_pnl: str
    entries: tuple[LifecycleEntryRow, ...]


@dataclass(frozen=True, slots=True)
class LifecycleExplorerSnapshot:
    rows: tuple[LifecycleRow, ...]
    selected_symbol: str | None

    @classmethod
    def initial(cls) -> "LifecycleExplorerSnapshot":
        return cls(rows=(), selected_symbol=None)


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    runtime: RuntimeSnapshot
    portfolio: PortfolioSnapshot
    activity: ActivitySnapshot
    positions: PositionsSnapshot
    orders: OrdersSnapshot
    decisions: DecisionCenterSnapshot
    runtime_health: HealthCenterSnapshot
    timeline: TimelineSnapshot
    lifecycle_explorer: LifecycleExplorerSnapshot
    operator_workspace: OperatorWorkspaceSnapshot
    replay: ReplaySnapshot
    recording: RecordingSnapshot
    event_store: EventStoreSnapshot
    analytics: AnalyticsSnapshot
    experiments: ExperimentSnapshot

    @classmethod
    def initial(cls) -> "DashboardSnapshot":
        return cls(
            runtime=RuntimeSnapshot.initial(),
            portfolio=PortfolioSnapshot.initial(),
            activity=ActivitySnapshot.initial(),
            positions=PositionsSnapshot.initial(),
            orders=OrdersSnapshot.initial(),
            decisions=DecisionCenterSnapshot.initial(),
            runtime_health=HealthCenterSnapshot.initial(),
            timeline=TimelineSnapshot.initial(),
            lifecycle_explorer=LifecycleExplorerSnapshot.initial(),
            operator_workspace=OperatorWorkspaceSnapshot.initial(),
            replay=ReplaySnapshot.initial(),
            recording=RecordingSnapshot.initial(),
            event_store=EventStoreSnapshot.initial(),
            analytics=AnalyticsSnapshot.initial(),
            experiments=ExperimentSnapshot.initial(),
        )
