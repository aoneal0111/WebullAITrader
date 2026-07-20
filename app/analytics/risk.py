from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from app.analytics.distribution import mean, population_standard_deviation
from app.analytics.models import AnalyticsConfig, EquityAnalytics, ReturnObservation, RiskAnalytics

ZERO = Decimal(0)
YEAR_MICROSECONDS = Decimal("31556952000000")


def analyze_risk(
    returns: tuple[ReturnObservation, ...], equity: EquityAnalytics,
    start_timestamp, end_timestamp, config: AnalyticsConfig,
) -> RiskAnalytics:
    _validate_config(config)
    values = tuple(item.return_value for item in returns)
    average = mean(values)
    deviation = population_standard_deviation(values)
    excess = tuple(value - config.risk_free_rate for value in values)
    excess_mean = mean(excess)
    excess_deviation = population_standard_deviation(excess)
    period_sharpe = excess_mean / excess_deviation if excess_mean is not None and excess_deviation not in (None, ZERO) else None
    downside = _downside_deviation(values, config.minimum_acceptable_return)
    sortino_mean = mean(tuple(value - config.minimum_acceptable_return for value in values))
    period_sortino = sortino_mean / downside if sortino_mean is not None and downside not in (None, ZERO) else None
    factor = _sqrt(Decimal(config.annualization_periods)) if config.annualization_periods is not None else None
    annualized_return = _annualized_return(equity.starting_equity, equity.ending_equity, start_timestamp, end_timestamp)
    return RiskAnalytics(
        average, deviation, downside, period_sharpe,
        period_sharpe * factor if period_sharpe is not None and factor is not None else None,
        period_sortino, period_sortino * factor if period_sortino is not None and factor is not None else None,
        annualized_return, deviation * factor if deviation is not None and factor is not None else None,
        annualized_return / equity.maximum_drawdown if annualized_return is not None and equity.maximum_drawdown != ZERO else None,
        config.return_interval, len(values),
    )


def _downside_deviation(values: tuple[Decimal, ...], minimum: Decimal) -> Decimal | None:
    if not values:
        return None
    squared = sum((min(value - minimum, ZERO) ** 2 for value in values), ZERO) / Decimal(len(values))
    return _sqrt(squared)


def _annualized_return(start: Decimal, end: Decimal, start_timestamp, end_timestamp) -> Decimal | None:
    elapsed = end_timestamp - start_timestamp
    microseconds = Decimal((elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds)
    if start <= ZERO or end <= ZERO or microseconds <= ZERO:
        return None
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return +(context.power(end / start, YEAR_MICROSECONDS / microseconds) - Decimal(1))


def _sqrt(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return +value.sqrt()


def _validate_config(config: AnalyticsConfig) -> None:
    if not isinstance(config, AnalyticsConfig):
        raise ValueError("analytics configuration is malformed")
    for name, value in (("annualization_periods", config.annualization_periods), ("rolling_window", config.rolling_window)):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            raise ValueError(f"{name} must be a positive integer when supplied")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in (config.risk_free_rate, config.minimum_acceptable_return)):
        raise ValueError("analytics rates must be finite Decimals")
    if not config.return_interval.strip():
        raise ValueError("return_interval must not be empty")
