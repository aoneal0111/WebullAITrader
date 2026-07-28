from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from .models import PerformanceMetrics, RiskMetrics, TimeMetrics
from .repository import HistoricalTrade


ZERO = Decimal("0")


def performance_metrics(
    trades: tuple[HistoricalTrade, ...],
) -> PerformanceMetrics:
    winners = tuple(trade.realized_pnl for trade in trades if trade.realized_pnl > 0)
    losers = tuple(trade.realized_pnl for trade in trades if trade.realized_pnl < 0)
    gross_profit = sum(winners, ZERO)
    gross_loss = sum(losers, ZERO)
    durations = tuple(
        trade.closed_at - trade.opened_at
        for trade in trades
        if trade.opened_at is not None
    )
    directional = len(winners) + len(losers)
    total = len(trades)
    return PerformanceMetrics(
        total_trades=total,
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=(
            Decimal(len(winners)) / Decimal(directional)
            if directional
            else ZERO
        ),
        average_gain=gross_profit / len(winners) if winners else ZERO,
        average_loss=gross_loss / len(losers) if losers else ZERO,
        profit_factor=(
            gross_profit / abs(gross_loss)
            if gross_loss
            else None
        ),
        expectancy=(
            sum((trade.realized_pnl for trade in trades), ZERO)
            / Decimal(total)
            if total
            else ZERO
        ),
        average_holding_duration=_average_duration(durations),
        average_trade_duration=_average_duration(durations),
        largest_winner=max(winners) if winners else ZERO,
        largest_loser=min(losers) if losers else ZERO,
        net_realized_pnl=gross_profit + gross_loss,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
    )


def risk_metrics(
    equity: tuple[tuple[datetime, Decimal], ...],
    exposures: tuple[Decimal, ...],
    largest_position: Decimal,
    net_realized_pnl: Decimal,
) -> RiskMetrics:
    peak = ZERO
    rolling: list[Decimal] = []
    for _, value in equity:
        peak = max(peak, value)
        rolling.append(peak - value)
    maximum = max(rolling, default=ZERO)
    ulcer = (
        (
            sum((value * value for value in rolling), ZERO)
            / Decimal(len(rolling))
        ).sqrt()
        if rolling
        else ZERO
    )
    return RiskMetrics(
        maximum_drawdown=maximum,
        rolling_drawdown=tuple(rolling),
        peak_equity=peak,
        recovery_factor=(
            net_realized_pnl / maximum
            if maximum
            else None
        ),
        ulcer_index=ulcer,
        average_exposure=(
            sum(exposures, ZERO) / Decimal(len(exposures))
            if exposures
            else ZERO
        ),
        largest_position=largest_position,
    )


def grouped_performance(
    trades: tuple[HistoricalTrade, ...],
    key: Callable[[HistoricalTrade], str],
) -> tuple[tuple[str, int, Decimal], ...]:
    grouped: dict[str, list[HistoricalTrade]] = {}
    for trade in trades:
        grouped.setdefault(key(trade), []).append(trade)
    return tuple(
        (
            name,
            len(items),
            sum((item.realized_pnl for item in items), ZERO),
        )
        for name, items in sorted(grouped.items())
    )


def time_metrics(
    trades: tuple[HistoricalTrade, ...],
) -> tuple[TimeMetrics, ...]:
    dimensions: tuple[
        tuple[str, Callable[[datetime], str]],
        ...,
    ] = (
        ("HOUR", lambda value: f"{value.hour:02d}:00"),
        ("DAY", lambda value: value.date().isoformat()),
        (
            "WEEK",
            lambda value: (
                f"{value.isocalendar().year}-W"
                f"{value.isocalendar().week:02d}"
            ),
        ),
        ("MONTH", lambda value: f"{value.year:04d}-{value.month:02d}"),
        ("TRADING_SESSION", _trading_session),
    )
    result: list[TimeMetrics] = []
    for dimension, key in dimensions:
        grouped: dict[str, list[HistoricalTrade]] = {}
        for trade in trades:
            grouped.setdefault(key(trade.closed_at), []).append(trade)
        result.extend(
            TimeMetrics(
                dimension=dimension,
                period=period,
                total_trades=len(items),
                winning_trades=sum(item.realized_pnl > 0 for item in items),
                realized_pnl=sum(
                    (item.realized_pnl for item in items),
                    ZERO,
                ),
            )
            for period, items in sorted(grouped.items())
        )
    return tuple(result)


def _average_duration(
    values: tuple[timedelta, ...],
) -> timedelta | None:
    if not values:
        return None
    return sum(values, timedelta()) / len(values)


def _trading_session(value: datetime) -> str:
    minutes = value.hour * 60 + value.minute
    if minutes < 9 * 60 + 30:
        return "PREMARKET"
    if minutes < 16 * 60:
        return "REGULAR"
    return "AFTER_HOURS"
