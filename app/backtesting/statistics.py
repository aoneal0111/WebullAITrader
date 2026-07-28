from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .models import MetricComparison


Comparable = Decimal | int | timedelta | None


def compare_metric(
    name: str,
    baseline: Comparable,
    candidate: Comparable,
) -> MetricComparison:
    delta = (
        candidate - baseline
        if baseline is not None and candidate is not None
        else None
    )
    return MetricComparison(name, baseline, candidate, delta)
