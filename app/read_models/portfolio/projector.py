from __future__ import annotations

from app.operations_core import (
    OperationsPortfolioHighlight,
    OperationsPortfolioSummary,
)
from app.read_models.portfolio.models import (
    PortfolioHighlight,
    PortfolioSummary,
)


def project_operational_portfolio(
    summary: OperationsPortfolioSummary,
) -> PortfolioSummary:
    if not isinstance(summary, OperationsPortfolioSummary):
        raise TypeError("summary must be an OperationsPortfolioSummary")
    return PortfolioSummary(
        total_market_value=summary.total_market_value,
        total_cost_basis=summary.total_cost_basis,
        realized_pnl=summary.realized_pnl,
        unrealized_pnl=summary.unrealized_pnl,
        total_pnl=summary.total_pnl,
        gross_exposure=summary.gross_exposure,
        long_exposure=summary.long_exposure,
        short_exposure=summary.short_exposure,
        open_positions=summary.open_positions,
        working_orders=summary.working_orders,
        winning_positions=summary.winning_positions,
        losing_positions=summary.losing_positions,
        largest_position=_highlight(summary.largest_position),
        largest_unrealized_gain=_highlight(
            summary.largest_unrealized_gain
        ),
        largest_unrealized_loss=_highlight(
            summary.largest_unrealized_loss
        ),
    )


def _highlight(
    value: OperationsPortfolioHighlight | None,
) -> PortfolioHighlight | None:
    if value is None:
        return None
    return PortfolioHighlight(symbol=value.symbol, value=value.value)
