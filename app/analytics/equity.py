from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.analytics.models import (
    DrawdownEpisode, DrawdownObservation, EquityAnalytics, ReturnObservation,
)
from app.paper_trading.models import EquityPoint

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def equity_curve_from_returns(starting_equity: Decimal, returns: tuple[ReturnObservation, ...]) -> tuple[EquityPoint, ...]:
    if not returns:
        raise ValueError("return observations must not be empty")
    points = [EquityPoint(returns[0].timestamp - timedelta(microseconds=1), starting_equity)]
    equity = starting_equity
    for item in returns:
        equity *= Decimal(1) + item.return_value
        points.append(EquityPoint(item.timestamp, equity))
    return tuple(points)


def equity_curve_from_trade_outcomes(starting_equity: Decimal, outcomes) -> tuple[EquityPoint, ...]:
    if not outcomes:
        raise ValueError("trade outcomes must not be empty")
    points = [EquityPoint(outcomes[0].close_timestamp - timedelta(microseconds=1), starting_equity)]
    equity = starting_equity
    for item in outcomes:
        equity += item.realized_pnl
        if equity <= ZERO:
            raise ValueError("scenario equity became nonpositive")
        points.append(EquityPoint(item.close_timestamp, equity))
    return tuple(points)


def validate_equity_curve(points: tuple[EquityPoint, ...]) -> tuple[EquityPoint, ...]:
    if not points:
        raise ValueError("equity curve must not be empty")
    previous: datetime | None = None
    for point in points:
        if not isinstance(point, EquityPoint) or point.timestamp.tzinfo is None:
            raise ValueError("equity observations require timezone-aware timestamps")
        if not isinstance(point.equity, Decimal) or not point.equity.is_finite() or point.equity <= ZERO:
            raise ValueError("equity must be a finite positive Decimal")
        if previous is not None and point.timestamp <= previous:
            raise ValueError("equity timestamps must be strictly increasing")
        previous = point.timestamp
    return points


def calculate_returns(points: tuple[EquityPoint, ...]) -> tuple[ReturnObservation, ...]:
    validate_equity_curve(points)
    return tuple(
        ReturnObservation(current.timestamp, current.equity / previous.equity - Decimal(1))
        for previous, current in zip(points[:-1], points[1:], strict=True)
    )


def period_end_equity(points: tuple[EquityPoint, ...], period: str) -> tuple[EquityPoint, ...]:
    validate_equity_curve(points)
    key_function: Callable[[datetime], object]
    if period == "daily":
        key_function = lambda value: value.astimezone(UTC).date()
    elif period == "weekly":
        key_function = lambda value: value.astimezone(UTC).isocalendar()[:2]
    elif period == "monthly":
        key_function = lambda value: (value.astimezone(UTC).year, value.astimezone(UTC).month)
    else:
        raise ValueError("period must be daily, weekly, or monthly")
    grouped: dict[object, EquityPoint] = {}
    for point in points:
        grouped[key_function(point.timestamp)] = point
    return tuple(grouped.values())


def calculate_daily_returns(points: tuple[EquityPoint, ...]) -> tuple[ReturnObservation, ...]:
    return calculate_returns(period_end_equity(points, "daily"))


def calculate_weekly_returns(points: tuple[EquityPoint, ...]) -> tuple[ReturnObservation, ...]:
    return calculate_returns(period_end_equity(points, "weekly"))


def calculate_monthly_returns(points: tuple[EquityPoint, ...]) -> tuple[ReturnObservation, ...]:
    return calculate_returns(period_end_equity(points, "monthly"))


def calculate_drawdown_observations(points: tuple[EquityPoint, ...]) -> tuple[DrawdownObservation, ...]:
    validate_equity_curve(points)
    peak = points[0].equity
    result = []
    for point in points:
        peak = max(peak, point.equity)
        result.append(DrawdownObservation(point.timestamp, point.equity, peak, Decimal(1) - point.equity / peak))
    return tuple(result)


def identify_drawdown_episodes(points: tuple[EquityPoint, ...]) -> tuple[DrawdownEpisode, ...]:
    validate_equity_curve(points)
    peak_point = points[0]
    trough: EquityPoint | None = None
    episodes: list[DrawdownEpisode] = []
    for point in points[1:]:
        if trough is None:
            if point.equity >= peak_point.equity:
                peak_point = point
            else:
                trough = point
        elif point.equity < trough.equity:
            trough = point
        elif point.equity >= peak_point.equity:
            episodes.append(_episode(peak_point, trough, point))
            peak_point = point
            trough = None
    if trough is not None:
        episodes.append(_episode(peak_point, trough, None))
    return tuple(episodes)


def analyze_equity(points: tuple[EquityPoint, ...]) -> EquityAnalytics:
    validate_equity_curve(points)
    returns = calculate_returns(points)
    observations = calculate_drawdown_observations(points)
    episodes = identify_drawdown_episodes(points)
    maximum = max((item.drawdown for item in observations), default=ZERO)
    average = sum((item.drawdown for item in episodes), ZERO) / Decimal(len(episodes)) if episodes else ZERO
    observed = _microseconds(points[-1].timestamp - points[0].timestamp)
    underwater = ZERO
    for left, right, drawdown in zip(points[:-1], points[1:], observations[:-1], strict=True):
        if drawdown.drawdown > ZERO:
            underwater += Decimal(_microseconds(right.timestamp - left.timestamp))
    longest = max(
        (
            item.total_underwater_duration_microseconds
            if item.total_underwater_duration_microseconds is not None
            else _microseconds(points[-1].timestamp - item.peak_timestamp)
            for item in episodes
        ),
        default=0,
    )
    return EquityAnalytics(
        points[0].equity, points[-1].equity, points[-1].equity / points[0].equity - Decimal(1),
        len(points), returns, episodes, maximum, average, longest,
        underwater / Decimal(observed) * HUNDRED if observed else None,
        observations[-1].drawdown, sum(item.recovery_timestamp is not None for item in episodes),
        sum(item.recovery_timestamp is None for item in episodes),
    )


def _episode(peak: EquityPoint, trough: EquityPoint, recovery: EquityPoint | None) -> DrawdownEpisode:
    decline = _microseconds(trough.timestamp - peak.timestamp)
    recovery_duration = _microseconds(recovery.timestamp - trough.timestamp) if recovery else None
    total = _microseconds(recovery.timestamp - peak.timestamp) if recovery else None
    return DrawdownEpisode(
        peak.timestamp, trough.timestamp, recovery.timestamp if recovery else None,
        peak.equity, trough.equity, Decimal(1) - trough.equity / peak.equity,
        decline, recovery_duration, total,
    )


def _microseconds(delta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
