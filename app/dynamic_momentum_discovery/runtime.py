"""Explicitly invoked research runner; not part of normal Atlas composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .evaluator import snapshot_from_rows
from .provider import (
    BroadDiscoveryRefresh,
    WebullBroadDiscoveryProvider,
    source_rows_by_symbol,
)
from .service import DynamicMomentumDiscoveryService


@dataclass(frozen=True, slots=True)
class CollectionResult:
    refresh: BroadDiscoveryRefresh | None
    assembled_symbols: int
    admitted_observations: int
    rejected_observations: int
    failure_type: str | None
    research_only: bool = True
    production_universe_mutated: bool = False
    execution_authorized: bool = False


class DynamicMomentumDiscoveryRunner:
    """Feeds only the dedicated research service and returns diagnostics."""

    def __init__(
        self, provider: WebullBroadDiscoveryProvider,
        service: DynamicMomentumDiscoveryService,
    ) -> None:
        self._provider = provider
        self._service = service

    def collect(
        self, *, breadth_per_source: int, observed_at: datetime, session: str,
        production_stages: Mapping[str, tuple[str, ...]] | None = None,
    ) -> CollectionResult:
        try:
            refresh = self._provider.fetch(
                breadth_per_source=breadth_per_source,
                observed_at=observed_at,
                session=session,
            )
            grouped = source_rows_by_symbol(refresh)
            accepted = 0
            rejected = 0
            stages = production_stages or {}
            for symbol, rows in grouped.items():
                try:
                    snapshot = snapshot_from_rows(
                        rows, decision_cutoff=observed_at, session=session,
                        production_stages=stages.get(symbol, ()),
                    )
                except Exception:
                    rejected += 1
                    continue
                if self._service.observe(snapshot):
                    accepted += 1
                else:
                    rejected += 1
            return CollectionResult(
                refresh=refresh, assembled_symbols=len(grouped),
                admitted_observations=accepted,
                rejected_observations=rejected, failure_type=None,
            )
        except Exception as exc:
            return CollectionResult(
                refresh=None, assembled_symbols=0, admitted_observations=0,
                rejected_observations=0, failure_type=type(exc).__name__,
            )


__all__ = ["CollectionResult", "DynamicMomentumDiscoveryRunner"]
