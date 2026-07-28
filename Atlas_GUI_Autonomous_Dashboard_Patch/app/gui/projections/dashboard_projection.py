from __future__ import annotations

from app.gui.formatters import format_orders, format_positions
from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    DashboardSnapshot,
    PortfolioSnapshot,
    RuntimeSnapshot,
    RuntimeState,
)
from app.operations_core import ApplicationState
from app.read_models.orders import project_orders_read_model
from app.read_models.positions import project_positions_read_model
from app.read_models.portfolio import project_portfolio_read_model


def project_dashboard(state: ApplicationState) -> DashboardSnapshot:
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
    )


def _money(value) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _percent(value) -> str:
    return f"{value:.2f}%"
