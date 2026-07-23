from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Callable

from app.momentum_scanner import (
    AssetClass,
    ScannerDecision,
    rank_candidates,
)
from app.realtime_scanner.models import (
    ReferenceWarmupFailure,
    ScannerSnapshot,
)
from app.realtime_scanner.protocols import (
    EventPipeline,
    ReferenceLoader,
    ReferenceSink,
    UniverseSelector,
)


class RealtimeScannerEngine:
    """
    Coordinates the eligible universe, reference-data warmup,
    market-event processing, and momentum ranking.

    This engine does not place, modify, or cancel orders.
    """

    def __init__(
        self,
        universe_service: UniverseSelector,
        reference_data_service: ReferenceLoader,
        pipeline: EventPipeline,
        *,
        reference_sink: ReferenceSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._universe_service = universe_service
        self._reference_data_service = reference_data_service
        self._pipeline = pipeline
        self._reference_sink = reference_sink
        self._clock = clock or _utc_now

        self._active_symbols: set[str] = set()
        self._active_asset_classes: dict[str, AssetClass] = {}
        self._decisions: dict[str, ScannerDecision] = {}
        self._reference_failures: list[ReferenceWarmupFailure] = []

        self._processed_events = 0
        self._ignored_events = 0

    def refresh_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        selection = self._universe_service.select_all(
            asset_classes
        )

        active_symbols: set[str] = set()
        active_asset_classes: dict[str, AssetClass] = {}
        failures: list[ReferenceWarmupFailure] = []

        for item in selection.included:
            symbol = item.symbol.strip().upper()

            try:
                record = self._reference_data_service.get(
                    symbol,
                    item.asset_class,
                    force_refresh=force_reference_refresh,
                )
            except Exception as exc:
                failures.append(
                    ReferenceWarmupFailure(
                        symbol=symbol,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            active_symbols.add(symbol)
            active_asset_classes[symbol] = item.asset_class

            if self._reference_sink is not None:
                self._reference_sink(record)

        removed_symbols = (
            self._active_symbols - active_symbols
        )

        for symbol in removed_symbols:
            self._decisions.pop(symbol, None)

        self._active_symbols = active_symbols
        self._active_asset_classes = active_asset_classes
        self._reference_failures = failures

        return self.active_symbols

    def consume(self, event: Any) -> ScannerDecision | None:
        symbol = _event_symbol(event)

        if symbol is None or symbol not in self._active_symbols:
            self._ignored_events += 1
            return None

        decision = self._pipeline.consume(event)
        self._processed_events += 1

        if decision is not None:
            normalized_symbol = decision.symbol.strip().upper()

            if normalized_symbol in self._active_symbols:
                self._decisions[normalized_symbol] = decision

        return decision

    def consume_many(
        self,
        events: Iterable[Any],
    ) -> tuple[ScannerDecision, ...]:
        updated: list[ScannerDecision] = []

        for event in events:
            decision = self.consume(event)

            if decision is not None:
                updated.append(decision)

        return tuple(updated)

    def ranked_candidates(
        self,
        *,
        limit: int = 25,
    ) -> tuple[ScannerDecision, ...]:
        return rank_candidates(
            self._decisions.values(),
            limit=limit,
        )

    def snapshot(
        self,
        *,
        limit: int = 25,
    ) -> ScannerSnapshot:
        timestamp = self._clock()

        if timestamp.tzinfo is None:
            raise ValueError(
                "scanner clock must return a timezone-aware datetime"
            )

        decisions = tuple(
            sorted(
                self._decisions.values(),
                key=lambda item: item.symbol,
            )
        )

        return ScannerSnapshot(
            timestamp=timestamp,
            active_symbols=self.active_symbols,
            decisions=decisions,
            ranked_candidates=self.ranked_candidates(
                limit=limit
            ),
            processed_events=self._processed_events,
            ignored_events=self._ignored_events,
            reference_failures=tuple(
                self._reference_failures
            ),
        )

    def clear_decisions(self) -> None:
        self._decisions.clear()

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_symbols))

    @property
    def processed_events(self) -> int:
        return self._processed_events

    @property
    def ignored_events(self) -> int:
        return self._ignored_events

    def asset_class_for(
        self,
        symbol: str,
    ) -> AssetClass | None:
        return self._active_asset_classes.get(
            symbol.strip().upper()
        )


def _event_symbol(event: Any) -> str | None:
    value = getattr(event, "symbol", None)

    if value is None:
        return None

    normalized = str(value).strip().upper()
    return normalized or None


def _utc_now() -> datetime:
    return datetime.now(UTC)
