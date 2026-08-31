from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Callable

from app.momentum_scanner import (
    AssetClass,
    ScannerDecision,
    rank_candidates,
)
from app.live_scanner.session import scanner_session
from app.realtime_scanner.models import (
    ReferenceWarmupFailure,
    ReferenceWarmupResult,
    ScannerSnapshot,
)
from app.reference_data.provider import UnsupportedReferenceSymbolError
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
        self._subscription_symbols: dict[str, str] = {}
        self._decisions: dict[str, ScannerDecision] = {}
        self._reference_failures: list[ReferenceWarmupFailure] = []
        self._warmup_result = ReferenceWarmupResult()
        self._universe_size = 0
        self._eligible_symbol_count = 0

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
        self._universe_size = len(selection.included) + len(selection.excluded)
        self._eligible_symbol_count = len(selection.included)

        active_symbols: set[str] = set()
        active_asset_classes: dict[str, AssetClass] = {}
        subscription_symbols: dict[str, str] = {}
        failures: list[ReferenceWarmupFailure] = []
        unsupported: list[ReferenceWarmupFailure] = []
        temporary: list[ReferenceWarmupFailure] = []
        missing: list[ReferenceWarmupFailure] = []
        successful_records = []

        for item in selection.included:
            symbol = item.symbol.strip().upper()

            try:
                get_for_instrument = getattr(
                    self._reference_data_service,
                    "get_for_instrument",
                    None,
                )
                if callable(get_for_instrument):
                    record = get_for_instrument(
                        item,
                        force_refresh=force_reference_refresh,
                    )
                else:
                    record = self._reference_data_service.get(
                        symbol,
                        item.asset_class,
                        force_refresh=force_reference_refresh,
                    )
            except Exception as exc:
                failure = _warmup_failure(symbol, exc)
                failures.append(failure)
                if failure.failure_type == "unsupported_symbol":
                    unsupported.append(failure)
                elif failure.failure_type == "missing_data":
                    missing.append(failure)
                else:
                    temporary.append(failure)
                continue

            successful_records.append(record)
            active_symbols.add(symbol)
            active_asset_classes[symbol] = item.asset_class
            subscription_symbols[symbol] = item.api_symbol or symbol

            if self._reference_sink is not None:
                self._reference_sink(record)

        self._warmup_result = ReferenceWarmupResult(
            active_symbols=tuple(sorted(active_symbols)),
            unsupported_rejections=tuple(unsupported),
            temporary_failures=tuple(temporary),
            missing_data_failures=tuple(missing),
            successful_records=tuple(successful_records),
        )

        removed_symbols = (
            self._active_symbols - active_symbols
        )

        for symbol in removed_symbols:
            self._decisions.pop(symbol, None)

        self._active_symbols = active_symbols
        self._active_asset_classes = active_asset_classes
        self._subscription_symbols = subscription_symbols
        self._reference_failures = failures

        return self.active_symbols

    def reset_stream_state(self) -> tuple[str, ...]:
        """Reset stream-derived state after a transport session replacement."""
        symbols = self.active_symbols
        reset_symbol = getattr(self._pipeline, "reset_symbol", None)
        if not callable(reset_symbol):
            return ()

        for symbol in symbols:
            reset_symbol(symbol)
            self._decisions.pop(symbol, None)

        return symbols

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

    def close(self) -> None:
        close = getattr(self._pipeline, "close", None)
        if callable(close):
            close()

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
            session=scanner_session(timestamp).value,
            warmup_result=self._warmup_result,
            healthy=bool(self._active_symbols),
            health_reason=(
                None
                if self._active_symbols
                else _empty_universe_reason(self._warmup_result)
            ),
            universe_size=self._universe_size,
            eligible_symbol_count=self._eligible_symbol_count,
        )

    def clear_decisions(self) -> None:
        self._decisions.clear()

    def diagnostic_results(self, *, limit: int = 3):
        diagnostics = getattr(self._pipeline, "diagnostic_results", None)
        return () if not callable(diagnostics) else diagnostics(limit=limit)

    def qualification_diagnostics(self, *, example_limit: int = 3):
        diagnostics = getattr(self._pipeline, "qualification_diagnostics", None)
        return (
            None
            if not callable(diagnostics)
            else diagnostics(example_limit=example_limit)
        )

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_symbols))

    @property
    def warmup_result(self) -> ReferenceWarmupResult:
        return self._warmup_result

    @property
    def subscription_symbols(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(self._subscription_symbols.values()))
        )

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


def _warmup_failure(symbol: str, exc: Exception) -> ReferenceWarmupFailure:
    environment = str(getattr(exc, "environment", "UNKNOWN")).upper()
    endpoint = str(getattr(exc, "endpoint", "stock_bars"))
    reason = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, UnsupportedReferenceSymbolError):
        return ReferenceWarmupFailure(
            symbol=symbol,
            reason="unsupported_symbol",
            failure_type="unsupported_symbol",
            environment=environment,
            endpoint=endpoint,
            retryable=False,
        )
    if isinstance(exc, (LookupError, ValueError)):
        return ReferenceWarmupFailure(
            symbol=symbol,
            reason=reason,
            failure_type="missing_data",
            environment=environment,
            endpoint=endpoint,
            retryable=False,
        )
    return ReferenceWarmupFailure(
        symbol=symbol,
        reason=reason,
        failure_type="temporary",
        environment=environment,
        endpoint=endpoint,
        retryable=True,
    )


def _empty_universe_reason(result: ReferenceWarmupResult) -> str:
    if result.unsupported_rejections:
        return "No scanner symbols are supported by the selected market-data environment."
    if result.temporary_failures:
        return "Scanner reference warmup is temporarily unavailable."
    if result.missing_data_failures:
        return "Scanner reference warmup returned no complete records."
    return "No eligible scanner symbols were discovered."
