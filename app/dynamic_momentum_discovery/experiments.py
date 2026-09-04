"""Deterministic breadth/selectivity summaries for offline research."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import DynamicMomentumObservation
from .outcomes import DynamicMomentumOutcome
from .provider import BroadDiscoveryRefresh


@dataclass(frozen=True, slots=True)
class BreadthResult:
    breadth_per_source: int
    unique_symbols: int
    production_overlap: int
    incremental_symbols: int
    churn_from_previous: int
    request_count: int
    rows_per_request: Decimal
    provider_latency_ms: float


@dataclass(frozen=True, slots=True)
class SelectivityResult:
    episode_count: int
    promoted_count: int
    positive_5m_count: int
    precision_proxy: Decimal | None


def summarize_breadths(
    refreshes: tuple[BroadDiscoveryRefresh, ...],
    *, production_symbols: frozenset[str],
) -> tuple[BreadthResult, ...]:
    results = []
    previous: frozenset[str] = frozenset()
    for refresh in sorted(refreshes, key=lambda item: item.breadth_per_source):
        symbols = frozenset(row.symbol for row in refresh.rows)
        overlap = len(symbols & production_symbols)
        results.append(BreadthResult(
            breadth_per_source=refresh.breadth_per_source,
            unique_symbols=len(symbols), production_overlap=overlap,
            incremental_symbols=len(symbols - production_symbols),
            churn_from_previous=len(symbols ^ previous) if previous else len(symbols),
            request_count=refresh.request_count,
            rows_per_request=(
                Decimal(refresh.returned_row_count) / Decimal(refresh.request_count)
                if refresh.request_count else Decimal("0")
            ),
            provider_latency_ms=refresh.request_latency_ms,
        ))
        previous = symbols
    return tuple(results)


def summarize_selectivity(
    observations: tuple[DynamicMomentumObservation, ...],
    outcomes: tuple[DynamicMomentumOutcome, ...],
) -> SelectivityResult:
    promoted = tuple(
        item for item in observations if item.shadow_promote_to_full_analysis
    )
    labels = {item.observation_id: item for item in outcomes}
    positive = sum(
        1 for item in promoted
        if item.observation_id in labels
        and labels[item.observation_id].return_5m_percent is not None
        and labels[item.observation_id].return_5m_percent > 0
    )
    labeled_promoted = sum(1 for item in promoted if item.observation_id in labels)
    return SelectivityResult(
        episode_count=len(observations), promoted_count=len(promoted),
        positive_5m_count=positive,
        precision_proxy=(
            Decimal(positive) / Decimal(labeled_promoted)
            if labeled_promoted else None
        ),
    )


__all__ = [
    "BreadthResult", "SelectivityResult", "summarize_breadths",
    "summarize_selectivity",
]
