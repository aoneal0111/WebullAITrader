from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

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
    OrdersSnapshot,
    PortfolioSnapshot,
    PositionsSnapshot,
    RuntimeSnapshot,
    RuntimeState,
    TimelineRow,
    TimelineSnapshot,
)
from app.operations_core import ApplicationState
from app.read_models.orders import project_orders_read_model
from app.read_models.operator_workspace import OperatorWorkspaceSnapshot
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
from app.replay import ReplaySnapshot


def project_dashboard(
    state: ApplicationState,
    decisions: DecisionsReadModelSnapshot | None = None,
    runtime_health: RuntimeHealthSnapshot | None = None,
    timeline: TimelineReadModelSnapshot | None = None,
    trade_lifecycle: TradeLifecycleSnapshot | None = None,
    operator_workspace: OperatorWorkspaceSnapshot | None = None,
    replay: ReplaySnapshot | None = None,
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
    if operator_workspace is None:
        operator_workspace = OperatorWorkspaceSnapshot.initial()
    if not isinstance(operator_workspace, OperatorWorkspaceSnapshot):
        raise TypeError(
            "operator_workspace must be an OperatorWorkspaceSnapshot"
        )
    if replay is None:
        replay = ReplaySnapshot.initial()
    if not isinstance(replay, ReplaySnapshot):
        raise TypeError("replay must be a ReplaySnapshot")

    runtime = state.runtime
    orders_read_model = project_orders_read_model(state)
    positions_read_model = project_positions_read_model(state)
    portfolio = project_portfolio_read_model(state)
    formatted_positions = format_positions(positions_read_model)
    formatted_orders = format_orders(orders_read_model)
    selected_symbol = operator_workspace.selected_symbol
    decision_rows = tuple(
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
            selection_id=_decision_selection_id(
                decisions.cycle,
                decision,
            ),
        )
        for decision in decisions.decisions
        if selected_symbol is None or decision.symbol == selected_symbol
    )
    timeline_rows = tuple(
        TimelineRow(
            time=f"{entry.timestamp.astimezone():%H:%M:%S}",
            category=entry.category.value,
            severity=entry.severity.value,
            summary=_timeline_summary(entry),
            selection_id=_timeline_selection_id(entry),
            symbol=entry.symbol,
        )
        for entry in timeline.entries
    )
    selected_timeline_entry = (
        operator_workspace.selected_timeline_entry
        or _first_timeline_for_symbol(timeline_rows, selected_symbol)
    )
    selected_order = (
        operator_workspace.selected_order
        or _first_order_for_symbol(
            orders_read_model.orders,
            selected_symbol,
        )
    )
    lifecycle_symbols = {
        lifecycle.symbol
        for lifecycle in trade_lifecycle.lifecycles
    }
    selected_trade_symbol = (
        selected_symbol
        if selected_symbol in lifecycle_symbols
        else trade_lifecycle.selected_symbol
    )

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
            selected_symbol=selected_symbol,
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
        positions=PositionsSnapshot(
            rows=formatted_positions.rows,
            symbols=tuple(
                position.symbol
                for position in positions_read_model.positions
            ),
            selected_symbol=(
                selected_symbol
                if selected_symbol in {
                    position.symbol
                    for position in positions_read_model.positions
                }
                else None
            ),
        ),
        orders=OrdersSnapshot(
            rows=formatted_orders.rows,
            symbols=tuple(
                order.symbol
                for order in orders_read_model.orders
            ),
            order_ids=tuple(
                order.order_id
                for order in orders_read_model.orders
            ),
            selected_order=selected_order,
        ),
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
            rows=decision_rows,
            selected_decision=operator_workspace.selected_decision,
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
            rows=timeline_rows,
            max_entries=timeline.max_entries,
            selected_entry=selected_timeline_entry,
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
            selected_symbol=selected_trade_symbol,
        ),
        operator_workspace=operator_workspace,
        replay=replay,
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


def _selection_hash(*parts: object) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return sha256(payload.encode("utf-8")).hexdigest()


def _decision_selection_id(cycle, decision) -> str:
    return _selection_hash(
        "decision",
        cycle,
        decision.symbol,
        decision.action,
        decision.decided_at.isoformat(),
        decision.strategy_version,
    )


def _timeline_selection_id(entry: TimelineReadModelEntry) -> str:
    return _selection_hash(
        "timeline",
        entry.timestamp.isoformat(),
        entry.category.value,
        entry.severity.value,
        entry.title,
        entry.description,
        entry.cycle,
        entry.symbol,
    )


def _first_timeline_for_symbol(
    rows: tuple[TimelineRow, ...],
    symbol: str | None,
) -> str | None:
    if symbol is None:
        return None
    return next(
        (
            row.selection_id
            for row in rows
            if row.symbol == symbol
        ),
        None,
    )


def _first_order_for_symbol(orders, symbol: str | None) -> str | None:
    if symbol is None:
        return None
    return next(
        (
            order.order_id
            for order in orders
            if order.symbol == symbol
        ),
        None,
    )
