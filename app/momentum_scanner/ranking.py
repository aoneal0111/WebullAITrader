from __future__ import annotations

from collections.abc import Iterable

from app.momentum_scanner.models import ScannerDecision


def rank_candidates(
    decisions: Iterable[ScannerDecision],
    *,
    limit: int = 25,
) -> tuple[ScannerDecision, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    qualified = (item for item in decisions if item.qualified)

    return tuple(
        sorted(
            qualified,
            key=lambda item: (
                -item.score,
                -item.metrics.relative_volume,
                -item.metrics.percentage_change,
                item.symbol,
            ),
        )[:limit]
    )
