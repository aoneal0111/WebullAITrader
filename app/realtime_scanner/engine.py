from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from threading import RLock
from time import perf_counter
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
from app.scanner_universe_observability import (
    UniverseAdmissionOutcome,
    UniverseAdmissionStage,
)
from app.performance_diagnostics import performance_diagnostics
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
        admission_observer: object | None = None,
    ) -> None:
        self._universe_service = universe_service
        self._reference_data_service = reference_data_service
        self._pipeline = pipeline
        self._reference_sink = reference_sink
        self._clock = clock or _utc_now
        self._admission_observer = admission_observer

        self._active_symbols: set[str] = set()
        self._known_symbols: set[str] = set()
        self._pending_reference_symbols: set[str] = set()
        self._active_asset_classes: dict[str, AssetClass] = {}
        self._subscription_symbols: dict[str, str] = {}
        self._decisions: dict[str, ScannerDecision] = {}
        self._reference_failures: list[ReferenceWarmupFailure] = []
        self._warmup_result = ReferenceWarmupResult()
        self._universe_size = 0
        self._eligible_symbol_count = 0

        self._processed_events = 0
        self._ignored_events = 0
        self._state_lock = RLock()
        self._prepared_selection = None
        self._reference_ready_observer: Callable[[], object] | None = None

    def prepare_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
    ) -> tuple[str, ...]:
        """Select symbols and expose subscriptions before reference warmup.

        Reference enrichment remains authoritative for qualification.  The
        returned symbols are only transport channels; pending symbols cannot
        become scanner decisions until their reference records are available.
        """
        performance_diagnostics.record_startup_stage("universe_refresh_started")
        performance_diagnostics.record_startup_stage("reference_warmup_started")
        selection = self._universe_service.select_all(asset_classes)
        included = _unique_symbols(selection.included)
        symbols = tuple(item.symbol.strip().upper() for item in included)
        with self._state_lock:
            self._prepared_selection = selection
            self._universe_size = len(selection.included) + len(selection.excluded)
            self._eligible_symbol_count = len(included)
            self._known_symbols = set(symbols)
            self._pending_reference_symbols = set(symbols)
            self._active_symbols = set()
            self._active_asset_classes = {
                symbol: item.asset_class for symbol, item in zip(symbols, included)
            }
            self._subscription_symbols = {
                symbol: item.api_symbol or symbol
                for symbol, item in zip(symbols, included)
            }
        performance_diagnostics.increment_startup_counter(
            "reference_warmup_symbols_total", len(included)
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_pending", len(included)
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_ready", 0
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_failed", 0
        )
        return self.subscription_symbols

    def refresh_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        performance_diagnostics.record_startup_stage("universe_refresh_started")
        performance_diagnostics.record_startup_stage("reference_warmup_started")
        selection = getattr(self, "_prepared_selection", None)
        selected_directly = selection is None
        if selection is None:
            selection = self._universe_service.select_all(asset_classes)
        self._prepared_selection = None
        included = _unique_symbols(selection.included)
        if selected_directly:
            performance_diagnostics.increment_startup_counter(
                "reference_warmup_symbols_total", len(included)
            )
        self._universe_size = len(selection.included) + len(selection.excluded)
        self._eligible_symbol_count = len(included)
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_pending", len(included)
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_ready", 0
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_failed", 0
        )

        active_symbols: set[str] = set()
        active_asset_classes: dict[str, AssetClass] = {}
        subscription_symbols: dict[str, str] = {}
        failures: list[ReferenceWarmupFailure] = []
        unsupported: list[ReferenceWarmupFailure] = []
        temporary: list[ReferenceWarmupFailure] = []
        missing: list[ReferenceWarmupFailure] = []
        successful_records = []

        for item in included:
            reference_started = perf_counter()
            reference_success = False
            reference_failure: str | None = None
            symbol = item.symbol.strip().upper()
            performance_diagnostics.set_startup_reference_symbol(symbol)
            _observe_admission(
                self._admission_observer,
                stage=UniverseAdmissionStage.REFERENCE_WARMUP_STARTED,
                outcome=UniverseAdmissionOutcome.STARTED,
                reason="EXISTING_REFERENCE_DATA_WARMUP",
                raw_symbol=item.display_symbol,
                normalized_symbol=symbol,
                upstream_fields={"api_symbol": item.api_symbol},
            )

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
                reference_failure = type(exc).__name__
                performance_diagnostics.increment_startup_counter(
                    "reference_warmup_symbols_completed"
                )
                performance_diagnostics.increment_startup_counter(
                    "reference_warmup_symbols_rejected"
                )
                failure = _warmup_failure(symbol, exc)
                _observe_admission(
                    self._admission_observer,
                    stage=UniverseAdmissionStage.REFERENCE_WARMUP_REJECTED,
                    outcome=UniverseAdmissionOutcome.REJECTED,
                    reason=failure.failure_type.upper(),
                    raw_symbol=item.display_symbol,
                    normalized_symbol=symbol,
                    upstream_fields={
                        "detail": failure.reason,
                        "environment": failure.environment,
                        "endpoint": failure.endpoint,
                        "retryable": failure.retryable,
                    },
                )
                failures.append(failure)
                if failure.failure_type == "unsupported_symbol":
                    unsupported.append(failure)
                elif failure.failure_type == "missing_data":
                    missing.append(failure)
                else:
                    temporary.append(failure)
                performance_diagnostics.record_reference_result(
                    (perf_counter() - reference_started) * 1000.0,
                    success=False,
                    failure=reference_failure,
                )
                with self._state_lock:
                    self._pending_reference_symbols.discard(symbol)
                    self._reference_failures = list(failures)
                    self._warmup_result = ReferenceWarmupResult(
                        active_symbols=tuple(sorted(self._active_symbols)),
                        temporary_failures=tuple(temporary),
                        unsupported_rejections=tuple(unsupported),
                        missing_data_failures=tuple(missing),
                    )
                performance_diagnostics.set_startup_counter(
                    "reference_warmup_symbols_pending",
                    len(self._pending_reference_symbols),
                )
                performance_diagnostics.set_startup_counter(
                    "reference_warmup_symbols_failed", len(failures)
                )
                continue

            successful_records.append(record)
            reference_success = True
            performance_diagnostics.increment_startup_counter(
                "reference_warmup_symbols_completed"
            )
            performance_diagnostics.increment_startup_counter(
                "reference_warmup_symbols_accepted"
            )
            _observe_admission(
                self._admission_observer,
                stage=UniverseAdmissionStage.REFERENCE_WARMUP_ACCEPTED,
                outcome=UniverseAdmissionOutcome.ACCEPTED,
                reason="REFERENCE_RECORD_AVAILABLE",
                raw_symbol=item.display_symbol,
                normalized_symbol=symbol,
                upstream_fields={
                    "api_symbol": item.api_symbol,
                    "reference_as_of": getattr(record, "as_of", None),
                },
            )
            active_symbols.add(symbol)
            active_asset_classes[symbol] = item.asset_class
            subscription_symbols[symbol] = item.api_symbol or symbol
            with self._state_lock:
                self._active_symbols.add(symbol)
                self._active_asset_classes[symbol] = item.asset_class
                self._pending_reference_symbols.discard(symbol)
                self._warmup_result = ReferenceWarmupResult(
                    active_symbols=tuple(sorted(self._active_symbols)),
                    successful_records=tuple(successful_records),
                )
            _observe_admission(
                self._admission_observer,
                stage=UniverseAdmissionStage.UNIVERSE_ADMITTED,
                outcome=UniverseAdmissionOutcome.ACCEPTED,
                reason="REFERENCE_WARMUP_SUCCEEDED",
                raw_symbol=item.display_symbol,
                normalized_symbol=symbol,
                upstream_fields={
                    "asset_class": item.asset_class.value,
                    "subscription_symbol": item.api_symbol or symbol,
                },
            )

            if self._reference_sink is not None:
                self._reference_sink(record)
            performance_diagnostics.record_reference_result(
                (perf_counter() - reference_started) * 1000.0,
                success=reference_success,
                cache_hit=None,
            )
            performance_diagnostics.record_startup_stage("first_reference_ready")
            performance_diagnostics.record_startup_stage("first_qualification_ready")
            performance_diagnostics.set_startup_counter(
                "reference_warmup_symbols_pending",
                len(self._pending_reference_symbols),
            )
            performance_diagnostics.set_startup_counter(
                "reference_warmup_symbols_ready", len(active_symbols)
            )
            if self._reference_ready_observer is not None:
                self._reference_ready_observer()

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

        with self._state_lock:
            self._active_symbols = active_symbols
            self._known_symbols = set(active_symbols)
            self._pending_reference_symbols = set()
            self._active_asset_classes = active_asset_classes
            self._subscription_symbols = subscription_symbols
            self._reference_failures = failures

        performance_diagnostics.set_startup_reference_symbol(None)
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_pending", 0
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_ready", len(active_symbols)
        )
        performance_diagnostics.set_startup_counter(
            "reference_warmup_symbols_failed", len(failures)
        )
        performance_diagnostics.record_startup_stage("all_references_terminal")
        performance_diagnostics.record_startup_stage("reference_warmup_completed")
        performance_diagnostics.record_startup_stage("universe_refresh_completed")

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

        if symbol is None or symbol not in self._known_symbols:
            self._ignored_events += 1
            return None

        _observe_admission(
            self._admission_observer,
            stage=UniverseAdmissionStage.SCANNER_EVALUATION_REACHED,
            outcome=UniverseAdmissionOutcome.REACHED,
            reason="ACTIVE_SYMBOL_EVENT_ENTERED_SCANNER_PIPELINE",
            raw_symbol=symbol,
            normalized_symbol=symbol,
            upstream_fields={
                "asset_class": getattr(
                    self._active_asset_classes.get(symbol), "value", None
                ),
            },
        )

        performance_diagnostics.increment_startup_counter(
            "scanner_market_events_received"
        )
        performance_diagnostics.record_startup_stage("first_scanner_ingestion")

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
        observer_close = getattr(self._admission_observer, "close", None)
        if callable(observer_close):
            try:
                observer_close()
            except Exception:
                pass

    def set_reference_ready_observer(
        self,
        observer: Callable[[], object] | None,
    ) -> None:
        if observer is not None and not callable(observer):
            raise TypeError("reference-ready observer must be callable or None")
        self._reference_ready_observer = observer

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

        population_metrics = getattr(self._pipeline, "population_metrics", None)
        if callable(population_metrics):
            values = population_metrics(
                active_symbols=tuple(sorted(self._active_symbols)),
                now=timestamp,
            )
            performance_diagnostics.record_scanner_population_base(
                active_symbols=len(self._active_symbols),
                adapter_state_count=int(values.get("adapter_state_count", 0)),
                missing_field_counts=values.get("missing_field_counts", {}),
                completeness_transitions=values.get(
                    "completeness_transitions", {}
                ),
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
    def pending_reference_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending_reference_symbols))

    @property
    def failed_reference_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(failure.symbol for failure in self._reference_failures))

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

    def memory_metrics(self) -> dict[str, int]:
        pipeline_metrics = getattr(self._pipeline, "memory_metrics", None)
        nested = {} if not callable(pipeline_metrics) else pipeline_metrics()
        return {
            "candidate_count": len(self._decisions),
            "subscription_count": len(self._subscription_symbols),
            "current_quote_symbol_count": len(self._active_symbols),
            "active_asset_class_count": len(self._active_asset_classes),
            "reference_failure_count": len(self._reference_failures),
            **{
                f"pipeline_{key}": value
                for key, value in nested.items()
            },
        }

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


def _unique_symbols(items: Iterable[Any]) -> tuple[Any, ...]:
    unique: list[Any] = []
    seen: set[str] = set()
    for item in items:
        symbol = str(item.symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        unique.append(item)
    return tuple(unique)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _observe_admission(observer: object | None, **values) -> None:
    callback = getattr(observer, "record", None)
    if not callable(callback):
        return
    try:
        callback(**values)
    except Exception:
        pass


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
