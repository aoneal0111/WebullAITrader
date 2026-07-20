from __future__ import annotations

from decimal import Decimal

from app.stress_testing.models import ComparisonThreshold, MetricComparison

ZERO = Decimal(0)
LOWER_IS_BETTER = {"maximum_drawdown", "loss_rate", "maximum_consecutive_losses"}


def compare_metric(metric: str, original: Decimal | None, scenario: Decimal | None,
                   tolerance: Decimal, thresholds: tuple[ComparisonThreshold, ...]) -> MetricComparison:
    if original is None or scenario is None:
        return MetricComparison(metric, original, scenario, None, None, "UNAVAILABLE", None)
    difference = scenario - original
    percentage = difference / abs(original) * Decimal(100) if original != ZERO else None
    directional = -difference if metric in LOWER_IS_BETTER else difference
    label = "EQUAL" if abs(difference) <= tolerance else "BETTER" if directional > ZERO else "WORSE"
    threshold = next((item for item in thresholds if item.metric == metric), None)
    passed = None if threshold is None else directional >= -threshold.maximum_adverse_difference
    return MetricComparison(metric, original, scenario, difference, percentage, label, passed)
