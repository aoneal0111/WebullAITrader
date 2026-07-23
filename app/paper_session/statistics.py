from __future__ import annotations

from decimal import Decimal

from app.execution_coordinator import (
    CoordinationStatus,
    ExecutionCoordinationResult,
)
from app.paper_session.models import (
    PaperSessionStatistics,
)
from app.paper_trading.models import (
    ExecutionStatus,
    PaperPortfolio,
)


ZERO = Decimal("0")


def initial_statistics(
    portfolio: PaperPortfolio,
) -> PaperSessionStatistics:
    return PaperSessionStatistics(
        decisions_processed=0,
        orders_attempted=0,
        orders_filled=0,
        orders_rejected=0,
        orders_not_filled=0,
        decisions_skipped=0,
        winning_fills=0,
        losing_fills=0,
        breakeven_fills=0,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl,
        current_equity=portfolio.equity,
        peak_equity=portfolio.equity,
        current_drawdown=ZERO,
    )


def advance_statistics(
    previous: PaperSessionStatistics,
    coordination: ExecutionCoordinationResult,
    portfolio: PaperPortfolio,
) -> PaperSessionStatistics:
    attempted = 0
    filled = 0
    rejected = 0
    not_filled = 0
    skipped = 0
    wins = 0
    losses = 0
    breakeven = 0

    if coordination.status is CoordinationStatus.SKIPPED:
        skipped = 1
    else:
        attempted = 1

    execution_result = coordination.execution_result

    if execution_result is None:
        if coordination.status is CoordinationStatus.REJECTED:
            rejected = 1
    else:
        execution = execution_result.execution
        status = execution.status

        if status is ExecutionStatus.FILLED:
            filled = 1
            fill = execution.fill

            if fill is not None:
                if fill.realized_pnl > ZERO:
                    wins = 1
                elif fill.realized_pnl < ZERO:
                    losses = 1
                else:
                    breakeven = 1
        elif status is ExecutionStatus.NOT_FILLED:
            not_filled = 1
        else:
            rejected = 1

    peak_equity = max(
        previous.peak_equity,
        portfolio.equity,
    )
    drawdown = peak_equity - portfolio.equity

    return PaperSessionStatistics(
        decisions_processed=(
            previous.decisions_processed + 1
        ),
        orders_attempted=(
            previous.orders_attempted + attempted
        ),
        orders_filled=(
            previous.orders_filled + filled
        ),
        orders_rejected=(
            previous.orders_rejected + rejected
        ),
        orders_not_filled=(
            previous.orders_not_filled + not_filled
        ),
        decisions_skipped=(
            previous.decisions_skipped + skipped
        ),
        winning_fills=(
            previous.winning_fills + wins
        ),
        losing_fills=(
            previous.losing_fills + losses
        ),
        breakeven_fills=(
            previous.breakeven_fills + breakeven
        ),
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl,
        current_equity=portfolio.equity,
        peak_equity=peak_equity,
        current_drawdown=drawdown,
    )
