from __future__ import annotations

from decimal import Decimal

from app.paper_trading.models import EquityPoint, JournalEventType, PaperJournal, PerformanceMetrics

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def calculate_metrics(journal: PaperJournal, equity_curve: tuple[EquityPoint, ...]) -> PerformanceMetrics:
    if not equity_curve or any(not _valid_point(point) for point in equity_curve):
        raise ValueError("equity curve is missing or malformed")
    initial = equity_curve[0].equity
    if initial <= ZERO:
        raise ValueError("initial equity must be positive")
    outcomes = tuple(
        Decimal(dict(event.details)["realized_pnl"])
        for event in journal.events
        if event.event_type is JournalEventType.FILL
        and "realized_pnl" in dict(event.details)
        and Decimal(dict(event.details)["realized_pnl"]) != ZERO
    )
    winners = tuple(value for value in outcomes if value > ZERO)
    losers = tuple(value for value in outcomes if value < ZERO)
    count = len(outcomes)
    win_rate = Decimal(len(winners)) / Decimal(count) * HUNDRED if count else ZERO
    average_winner = sum(winners, ZERO) / Decimal(len(winners)) if winners else None
    average_loser = sum(losers, ZERO) / Decimal(len(losers)) if losers else None
    gross_profit = sum(winners, ZERO)
    gross_loss = abs(sum(losers, ZERO))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    expectancy = sum(outcomes, ZERO) / Decimal(count) if count else None
    total_return = (equity_curve[-1].equity - initial) / initial * HUNDRED
    peak = equity_curve[0].equity
    maximum_drawdown = ZERO
    for point in equity_curve:
        peak = max(peak, point.equity)
        drawdown = (peak - point.equity) / peak * HUNDRED if peak > ZERO else ZERO
        maximum_drawdown = max(maximum_drawdown, drawdown)
    return PerformanceMetrics(win_rate, average_winner, average_loser, profit_factor, expectancy, total_return, maximum_drawdown)


def _valid_point(point: EquityPoint) -> bool:
    return point.timestamp.tzinfo is not None and point.equity.is_finite() and point.equity >= ZERO
