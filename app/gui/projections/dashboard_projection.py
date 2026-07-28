from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.gui.formatters import format_orders, format_positions
from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    DashboardSnapshot,
    DecisionCenterSnapshot,
    DecisionRow,
    HealthBadgeSnapshot,
    HealthCenterSnapshot,
    LifecycleEntryRow,
    LifecycleExplorerSnapshot,
    LifecycleRow,
    PortfolioSnapshot,
    RuntimeSnapshot,
    RuntimeState,
    TimelineRow,
    TimelineSnapshot,
)
from app.operations_core import ApplicationState
from app.read_models.orders import project_orders_read_model
from app.read_models.positions import project_positions_read_model
from app.read_models.portfolio import project_portfolio_read_model
from app.read_models.decisions import DecisionsReadModelSnapshot
from app.read_models.runtime_health import (
    OverallHealth,
    RuntimeHealthSnapshot,
    SubsystemHealth,
)
from app.read_models.timeline import (
    TimelineEntry as TimelineReadModelEntry,
    TimelineReadModelSnapshot,
)
from app.read_models.trade_lifecycle import (
    TradeLifecycleEntry,
    TradeLifecycleSnapshot,
)


def project_dashboard(
    state: ApplicationState,
    decisions: DecisionsReadModelSnapshot | None = None,
    runtime_health: RuntimeHealthSnapshot | None = None,
    timeline: TimelineReadModelSnapshot | None = None,
    trade_lifecycle: TradeLifecycleSnapshot | None = None,
) -> DashboardSnapshot:
    if not isinstance(state, ApplicationState):
        raise TypeError("state must be an ApplicationState")
    if decisions is None:
        decisions = DecisionsReadModelSnapshot.initial()
    if not isinstance(decisions, DecisionsReadModelSnapshot):
        raise TypeError("decisions must be a DecisionsReadModelSnapshot")
    if runtime_health is None:
        runtime_health = RuntimeHealthSnapshot.initial()
    if not isinstance(runtime_health, RuntimeHealthSnapshot):
        raise TypeError("runtime_health must be a RuntimeHealthSnapshot")
    if timeline is None:
        timeline = TimelineReadModelSnapshot.initial()
    if not isinstance(timeline, TimelineReadModelSnapshot):
        raise TypeError("timeline must be a TimelineReadModelSnapshot")
    if trade_lifecycle is None:
        trade_lifecycle = TradeLifecycleSnapshot.initial()
    if not isinstance(trade_lifecycle, TradeLifecycleSnapshot):
        raise TypeError(
            "trade_lifecycle must be a TradeLifecycleSnapshot"
        )

    runtime = state.runtime
    orders_read_model = project_orders_read_model(state)
    positions_read_model = project_positions_read_model(state)
    portfolio = project_portfolio_read_model(state)

    return DashboardSnapshot(
        runtime=RuntimeSnapshot(
            environment=runtime.environment,
            state=RuntimeState(runtime.phase.value),
            broker_status=runtime.broker_status,
            market_feed_status=runtime.market_feed_status,
            inference_status=runtime.inference_status,
            emergency_stop_enabled=True,
            active_model=runtime.active_model,
            cycle_count=runtime.cycles_completed,
            status_message=runtime.last_error or "Healthy",
        ),
        portfolio=PortfolioSnapshot(
            equity=_money(portfolio.equity),
            realized_pnl=_money(portfolio.realized_pnl),
            unrealized_pnl=_money(portfolio.unrealized_pnl),
            current_drawdown=_percent(portfolio.current_drawdown),
            total_return=_percent(portfolio.total_return),
            win_rate=_percent(portfolio.win_rate),
            order_count=portfolio.order_count,
            position_count=portfolio.position_count,
        ),
        activity=ActivitySnapshot(
            entries=tuple(
                ActivityEntry(
                    occurred_at=entry.occurred_at,
                    message=entry.message,
                )
                for entry in state.timeline[-10:][::-1]
            )
        ),
        positions=format_positions(positions_read_model),
        orders=format_orders(orders_read_model),
        decisions=DecisionCenterSnapshot(
            cycle=(
                "Awaiting first cycle"
                if decisions.cycle is None
                else f"Cycle {decisions.cycle}"
            ),
            updated_at=(
                "No decisions projected"
                if decisions.updated_at is None
                else f"Updated {decisions.updated_at.astimezone():%H:%M:%S}"
            ),
            rows=tuple(
                DecisionRow(
                    symbol=decision.symbol,
                    action=decision.action.replace("_", " "),
                    confidence=f"{decision.confidence}%",
                    score=f"{decision.score:f}",
                    rationale=(
                        " | ".join(decision.reasons)
                        if decision.reasons
                        else "No rationale supplied"
                    ),
                    decided_at=f"{decision.decided_at.astimezone():%H:%M:%S}",
                )
                for decision in decisions.decisions
            ),
        ),
        runtime_health=HealthCenterSnapshot(
            overall_health=_health_badge(
                "Overall Health",
                runtime_health.overall_health.value,
                runtime_health.overall_health,
            ),
            runtime_state=_health_badge(
                "Runtime State",
                runtime_health.runtime_state,
                runtime_health.overall_health,
            ),
            broker_status=_subsystem_badge(
                "Broker Status",
                runtime_health.broker,
            ),
            scanner_status=_subsystem_badge(
                "Scanner Status",
                runtime_health.scanner,
            ),
            market_data_status=_subsystem_badge(
                "Market Data Status",
                runtime_health.market_data,
            ),
            operations_bus_status=_subsystem_badge(
                "Operations Bus Status",
                runtime_health.operations_bus,
            ),
            current_cycle=HealthBadgeSnapshot(
                "Current Cycle",
                str(runtime_health.current_cycle.value),
                "neutral",
            ),
            last_completed_cycle=HealthBadgeSnapshot(
                "Last Completed Cycle",
                str(runtime_health.last_completed_cycle.value),
                "neutral",
            ),
            last_update_time=HealthBadgeSnapshot(
                "Last Update Time",
                (
                    "NEVER"
                    if runtime_health.last_update_time is None
                    else f"{runtime_health.last_update_time.astimezone():%H:%M:%S}"
                ),
                "neutral",
            ),
            warnings=tuple(
                HealthBadgeSnapshot("Warning", warning, "warn")
                for warning in runtime_health.warnings
            ),
            errors=tuple(
                HealthBadgeSnapshot("Error", error, "danger")
                for error in runtime_health.errors
            ),
        ),
        timeline=TimelineSnapshot(
            rows=tuple(
                TimelineRow(
                    time=f"{entry.timestamp.astimezone():%H:%M:%S}",
                    category=entry.category.value,
                    severity=entry.severity.value,
                    summary=_timeline_summary(entry),
                )
                for entry in timeline.entries
            ),
            max_entries=timeline.max_entries,
        ),
        lifecycle_explorer=LifecycleExplorerSnapshot(
            rows=tuple(
                LifecycleRow(
                    symbol=lifecycle.symbol,
                    status=lifecycle.status.value,
                    opened=_optional_time(lifecycle.opened_at),
                    closed=_optional_time(lifecycle.closed_at),
                    realized_pnl=_signed_money(lifecycle.realized_pnl),
                    entries=tuple(
                        LifecycleEntryRow(
                            time=_optional_time(entry.timestamp),
                            phase=entry.phase.value.replace("_", " "),
                            summary=_lifecycle_summary(entry),
                        )
                        for entry in lifecycle.entries
                    ),
                )
                for lifecycle in trade_lifecycle.lifecycles
            ),
            selected_symbol=trade_lifecycle.selected_symbol,
        ),
    )


def _money(value) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _percent(value) -> str:
    return f"{value:.2f}%"


def _subsystem_badge(
    label: str,
    subsystem: SubsystemHealth,
) -> HealthBadgeSnapshot:
    return _health_badge(
        label,
        subsystem.status,
        subsystem.health,
    )


def _health_badge(
    label: str,
    value: str,
    health: OverallHealth,
) -> HealthBadgeSnapshot:
    levels = {
        OverallHealth.UNKNOWN: "neutral",
        OverallHealth.HEALTHY: "good",
        OverallHealth.DEGRADED: "warn",
        OverallHealth.UNHEALTHY: "danger",
    }
    return HealthBadgeSnapshot(label, value, levels[health])


def _timeline_summary(entry: TimelineReadModelEntry) -> str:
    context = tuple(
        value
        for value in (
            None if entry.cycle is None else f"Cycle {entry.cycle}",
            entry.symbol,
        )
        if value is not None
    )
    suffix = "" if not context else f" ({' | '.join(context)})"
    return f"{entry.title}: {entry.description}{suffix}"


def _optional_time(value: datetime | None) -> str:
    return "--" if value is None else f"{value.astimezone():%H:%M:%S}"


def _lifecycle_summary(entry: TradeLifecycleEntry) -> str:
    identifiers = tuple(
        value
        for value in (
            None if entry.order_id is None else f"Order {entry.order_id}",
            (
                None
                if entry.position_id is None
                else f"Position {entry.position_id}"
            ),
            None if entry.cycle is None else f"Cycle {entry.cycle}",
        )
        if value is not None
    )
    suffix = "" if not identifiers else f" ({' | '.join(identifiers)})"
    return f"{entry.title}: {entry.description}{suffix}"


def _signed_money(value: Decimal) -> str:
    if value > 0:
        return f"+${value:,.2f}"
    return _money(value)
