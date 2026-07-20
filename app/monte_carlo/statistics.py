from __future__ import annotations

from decimal import Decimal

from app.analytics.distribution import mean, median, percentile, population_standard_deviation
from app.monte_carlo.models import MetricSummary


def summarize(values: tuple[Decimal, ...]) -> MetricSummary:
    if not values:
        raise ValueError("metric observations must not be empty")
    return MetricSummary(
        mean(values), median(values), min(values), max(values), population_standard_deviation(values),
        percentile(values, Decimal("0.05")), percentile(values, Decimal("0.25")),
        percentile(values, Decimal("0.50")), percentile(values, Decimal("0.75")),
        percentile(values, Decimal("0.95")),
    )


def probability(matches: tuple[bool, ...]) -> Decimal:
    if not matches:
        raise ValueError("probability observations must not be empty")
    return Decimal(sum(matches)) / Decimal(len(matches)) * Decimal(100)
