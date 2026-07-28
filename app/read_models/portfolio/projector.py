from __future__ import annotations

from app.operations_core import ApplicationState
from app.read_models.portfolio.models import PortfolioReadModelSnapshot


def project_portfolio_read_model(state: ApplicationState) -> PortfolioReadModelSnapshot:
    """Project authoritative application state into a portfolio summary."""
    if not isinstance(state, ApplicationState):
        raise TypeError("state must be an ApplicationState")
    runtime = state.paper_runtime
    if runtime is None:
        return PortfolioReadModelSnapshot(
            order_count=len(state.orders),
            position_count=len(state.positions),
        )
    return PortfolioReadModelSnapshot(
        timestamp=runtime.timestamp,
        session_id=runtime.session_id,
        equity=runtime.current_equity,
        peak_equity=runtime.peak_equity,
        realized_pnl=runtime.realized_pnl,
        unrealized_pnl=runtime.unrealized_pnl,
        current_drawdown=runtime.current_drawdown,
        total_return=runtime.total_return,
        maximum_drawdown=runtime.maximum_drawdown,
        win_rate=runtime.win_rate,
        order_count=len(state.orders),
        position_count=len(state.positions),
    )
