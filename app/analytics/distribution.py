from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from decimal import Decimal, ROUND_FLOOR, localcontext

from app.analytics.models import (
    DistributionAnalytics, RealizedPnlGroup, ReturnObservation, RollingObservation, TradeOutcome,
)

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def mean(values: tuple[Decimal, ...]) -> Decimal | None:
    return sum(values, ZERO) / Decimal(len(values)) if values else None


def median(values: tuple[Decimal, ...]) -> Decimal | None:
    return percentile(values, Decimal("0.5"))


def percentile(values: tuple[Decimal, ...], probability: Decimal) -> Decimal | None:
    if not isinstance(probability, Decimal) or not probability.is_finite() or not ZERO <= probability <= Decimal(1):
        raise ValueError("percentile probability must be between zero and one")
    if not values:
        return None
    ordered = tuple(sorted(_validate_values(values)))
    position = Decimal(len(ordered) - 1) * probability
    lower = int(position.to_integral_value(rounding=ROUND_FLOOR))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    return ordered[lower] + weight * (ordered[upper] - ordered[lower])


def population_variance(values: tuple[Decimal, ...]) -> Decimal | None:
    values = _validate_values(values)
    average = mean(values)
    return sum(((value - average) ** 2 for value in values), ZERO) / Decimal(len(values)) if average is not None else None


def population_standard_deviation(values: tuple[Decimal, ...]) -> Decimal | None:
    variance = population_variance(values)
    if variance is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return +variance.sqrt()


def analyze_distribution(values: tuple[Decimal, ...]) -> DistributionAnalytics:
    values = _validate_values(values)
    if not values:
        return DistributionAnalytics(0, *([None] * 16))
    average = mean(values)
    deviation = population_standard_deviation(values)
    skewness = None
    kurtosis = None
    if deviation and deviation != ZERO:
        if len(values) >= 3:
            skewness = sum(((value - average) ** 3 for value in values), ZERO) / Decimal(len(values)) / deviation ** 3
        if len(values) >= 4:
            kurtosis = sum(((value - average) ** 4 for value in values), ZERO) / Decimal(len(values)) / deviation ** 4 - Decimal(3)
    probabilities = tuple(Decimal(item) for item in ("0.01", "0.05", "0.10", "0.25", "0.50", "0.75", "0.90", "0.95", "0.99"))
    percentiles = tuple(percentile(values, probability) for probability in probabilities)
    return DistributionAnalytics(
        len(values), min(values), max(values), average, median(values), deviation,
        *percentiles, skewness, kurtosis,
    )


def group_realized_pnl(outcomes: tuple[TradeOutcome, ...], grouping: str) -> tuple[RealizedPnlGroup, ...]:
    key_function: Callable[[TradeOutcome], str]
    if grouping == "month":
        key_function = lambda item: item.close_timestamp.astimezone(UTC).strftime("%Y-%m")
    elif grouping == "weekday":
        key_function = lambda item: str(item.close_timestamp.astimezone(UTC).isoweekday())
    elif grouping == "hour":
        key_function = lambda item: str(item.close_timestamp.astimezone(UTC).hour)
    else:
        raise ValueError("grouping must be month, weekday, or hour")
    grouped: dict[str, list[TradeOutcome]] = {}
    for outcome in sorted(outcomes, key=lambda item: (item.close_timestamp, item.journal_sequence)):
        grouped.setdefault(key_function(outcome), []).append(outcome)
    rows = []
    for key in sorted(grouped, key=lambda item: int(item) if item.isdigit() else item):
        items = grouped[key]
        total = sum((item.realized_pnl for item in items), ZERO)
        rows.append(RealizedPnlGroup(key, len(items), total, total / Decimal(len(items)),
                                     Decimal(sum(item.is_win for item in items)) / Decimal(len(items)) * HUNDRED))
    return tuple(rows)


def rolling_trade_metric(outcomes: tuple[TradeOutcome, ...], window: int, metric: str) -> tuple[RollingObservation, ...]:
    _validate_window(window)
    ordered = tuple(sorted(outcomes, key=lambda item: (item.close_timestamp, item.journal_sequence)))
    result = []
    for index in range(window - 1, len(ordered)):
        sample = ordered[index - window + 1:index + 1]
        if metric == "win_rate":
            directional = tuple(item for item in sample if not item.is_breakeven)
            value = Decimal(sum(item.is_win for item in directional)) / Decimal(len(directional)) * HUNDRED if directional else None
        elif metric == "expectancy":
            value = sum((item.realized_pnl for item in sample), ZERO) / Decimal(window)
        else:
            raise ValueError("unsupported rolling trade metric")
        result.append(RollingObservation(sample[-1].close_timestamp, window, value))
    return tuple(result)


def rolling_return_metric(returns: tuple[ReturnObservation, ...], window: int, metric: str) -> tuple[RollingObservation, ...]:
    _validate_window(window)
    result = []
    for index in range(window - 1, len(returns)):
        sample = returns[index - window + 1:index + 1]
        values = tuple(item.return_value for item in sample)
        value = mean(values) if metric == "mean" else population_standard_deviation(values) if metric == "volatility" else None
        if metric not in ("mean", "volatility"):
            raise ValueError("unsupported rolling return metric")
        result.append(RollingObservation(sample[-1].timestamp, window, value))
    return tuple(result)


def _validate_values(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("distribution values must be finite Decimals")
    return values


def _validate_window(window: int) -> None:
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("rolling window must be a positive integer")
