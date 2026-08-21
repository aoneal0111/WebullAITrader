"""Bounded, market-data-only diagnostic for the autonomous scanner.

The production composition in this module deliberately has no broker, account,
order, execution, risk, journal, or persistence dependency.  It connects only
to Webull market data and configured catalyst sources, observes a bounded event
window, emits sanitized JSON, and closes the stream before returning.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
import time as time_module
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.catalysts import CatalystAggregator, build_catalyst_providers
from app.catalysts.models import CatalystAggregationResult
from app.configuration import OperationalConfiguration, load_configuration
from app.live_scanner.coordinator import LiveScannerCoordinator
from app.live_scanner.transport import ReceiveTransportAdapter
from app.market.calendar import (
    EASTERN,
    MarketSession,
    TradingDaySchedule,
    market_session,
    trading_day_schedule,
)
from app.momentum_scanner import AssetClass, CatalystStatus
from app.reference_data import ReferenceDataCache, ReferenceDataService
from app.reference_data.models import ReferenceRecord
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.scanner_adapter import (
    AdapterResult,
    MarketEventScannerAdapter,
    MomentumScannerPipeline,
    ScannerReferenceData,
    ScannerReferenceStore,
)
from app.universe import UniverseService
from app.universe.models import UniverseSelection
from app.webull.client_factories import (
    MarketDataClientFactory,
    market_data_cache_scope,
    market_data_configuration,
)
from app.webull.credential_identity import credential_fingerprint
from app.webull.request_audit import RequestIdentity, RequestService
from app.webull.configuration import ReconnectPolicy
from app.webull.logging import StructuredLogger
from app.webull.market_event_parser import WebullMarketEventParser
from app.webull.sdk_streaming_adapter import (
    WebullStreamingCredentials,
    create_official_market_subscription,
    create_official_stream_backend,
)
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerReferenceProvider,
    WebullScannerUniverseProvider,
)
from app.webull.stream_endpoint import select_official_sdk_stream_endpoint
from app.webull.websocket_client import WebullWebSocketClient


Clock = Callable[[], datetime]
Monotonic = Callable[[], float]
EventSink = Callable[[Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class DiagnosticLimits:
    duration_seconds: float = 30.0
    maximum_events: int = 1_000
    maximum_symbols: int = 25

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive and finite")
        if self.maximum_events <= 0:
            raise ValueError("maximum_events must be positive")
        if self.maximum_symbols <= 0 or self.maximum_symbols > 100:
            raise ValueError("maximum_symbols must be in 1..100")


@dataclass(slots=True)
class RequestCounters:
    webull_market_data_requests: int = 0
    webull_market_data_rest_requests: int = 0
    webull_catalyst_requests: int = 0
    yahoo_requests: int = 0
    cnbc_feed_requests: int = 0
    marketwatch_feed_requests: int = 0
    sec_requests: int = 0
    webull_stream_connects: int = 0
    webull_stream_subscriptions: int = 0
    provider_calls: dict[str, int] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)
    cache_misses: dict[str, int] = field(default_factory=dict)
    cache_indeterminate: dict[str, int] = field(default_factory=dict)

    def request_total(self, provider_name: str) -> int:
        return {
            "YAHOO_FINANCE": self.yahoo_requests,
            "CNBC": self.cnbc_feed_requests,
            "MARKETWATCH": self.marketwatch_feed_requests,
            "SEC_EDGAR": self.sec_requests,
            "WEBULL_EARNINGS_SEC": self.webull_catalyst_requests,
        }.get(provider_name, 0)

    def as_dict(self) -> dict[str, object]:
        return {
            "webull_market_data_requests": self.webull_market_data_requests,
            "webull_market_data_rest_requests": (
                self.webull_market_data_rest_requests
            ),
            "webull_catalyst_requests": self.webull_catalyst_requests,
            "yahoo_requests": self.yahoo_requests,
            "cnbc_feed_requests": self.cnbc_feed_requests,
            "marketwatch_feed_requests": self.marketwatch_feed_requests,
            "sec_requests": self.sec_requests,
            "webull_stream_connects": self.webull_stream_connects,
            "webull_stream_subscriptions": self.webull_stream_subscriptions,
            "provider_calls": dict(sorted(self.provider_calls.items())),
            "cache_hits": dict(sorted(self.cache_hits.items())),
            "cache_misses": dict(sorted(self.cache_misses.items())),
            "cache_indeterminate": dict(
                sorted(self.cache_indeterminate.items())
            ),
            "snapshot_based": {
                "CNBC": True,
                "MARKETWATCH": True,
            },
        }


@dataclass(slots=True)
class DiagnosticTimings:
    configuration_composition_seconds: float = 0.0
    universe_discovery_seconds: float = 0.0
    reference_warmup_seconds: float = 0.0
    catalyst_seconds: float = 0.0
    stream_observation_seconds: float = 0.0
    total_seconds: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "configuration_composition_seconds": self.configuration_composition_seconds,
            "universe_discovery_seconds": self.universe_discovery_seconds,
            "reference_warmup_seconds": self.reference_warmup_seconds,
            "catalyst_seconds": self.catalyst_seconds,
            "stream_observation_seconds": self.stream_observation_seconds,
            "total_diagnostic_seconds": self.total_seconds,
        }


@dataclass(slots=True)
class DiagnosticRuntime:
    configuration: OperationalConfiguration
    infrastructure: "DiagnosticScannerInfrastructure"
    adapter: MarketEventScannerAdapter
    references: dict[str, ReferenceRecord]
    catalyst_results: dict[str, CatalystAggregationResult]
    counters: RequestCounters
    timings: DiagnosticTimings
    universe: "BoundedUniverseSelector"


@dataclass(frozen=True, slots=True)
class DiagnosticScannerInfrastructure:
    transport: ReceiveTransportAdapter
    pipeline: MomentumScannerPipeline
    engine: RealtimeScannerEngine
    coordinator: LiveScannerCoordinator


class _NullSink:
    def emit(self, _record: object) -> None:
        return None


class BoundedUniverseSelector:
    """Retain real discovery counts while bounding reference/subscription work."""

    def __init__(
        self,
        base: UniverseService,
        maximum_symbols: int,
        timings: DiagnosticTimings,
        *,
        monotonic: Monotonic = time_module.monotonic,
    ) -> None:
        self._base = base
        self._maximum_symbols = maximum_symbols
        self._timings = timings
        self._monotonic = monotonic
        self.discovered_count = 0
        self.eligible_count = 0
        self.selected_count = 0

    def select_all(self, asset_classes=(AssetClass.STOCK,)) -> UniverseSelection:
        started = self._monotonic()
        selection = self._base.select_all(asset_classes)
        self._timings.universe_discovery_seconds += self._monotonic() - started
        self.discovered_count = len(selection.included) + len(selection.excluded)
        self.eligible_count = len(selection.included)
        included = selection.included[: self._maximum_symbols]
        omitted = selection.included[self._maximum_symbols :]
        self.selected_count = len(included)
        return UniverseSelection(
            included=included,
            excluded=selection.excluded + omitted,
        )


class TimedReferenceDataService:
    def __init__(
        self,
        inner: ReferenceDataService,
        timings: DiagnosticTimings,
        *,
        monotonic: Monotonic = time_module.monotonic,
    ) -> None:
        self._inner = inner
        self._timings = timings
        self._monotonic = monotonic

    def get_for_instrument(self, instrument, *, force_refresh=False):
        started = self._monotonic()
        try:
            return self._inner.get_for_instrument(
                instrument, force_refresh=force_refresh
            )
        finally:
            self._timings.reference_warmup_seconds += (
                self._monotonic() - started
            )


class RecordingCatalystAggregator:
    def __init__(self, inner: CatalystAggregator) -> None:
        self._inner = inner
        self.results: dict[str, CatalystAggregationResult] = {}

    def aggregate_result(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystAggregationResult:
        result = self._inner.aggregate_result(symbol, as_of)
        self.results[symbol.strip().upper()] = result
        return result

    def get_evidence(self, symbol: str, as_of: datetime | None = None):
        return self.aggregate_result(symbol, as_of).selected


class ObservedCatalystProvider:
    def __init__(
        self,
        inner: object,
        counters: RequestCounters,
        timings: DiagnosticTimings,
        *,
        monotonic: Monotonic = time_module.monotonic,
    ) -> None:
        self._inner = inner
        self.name = str(getattr(inner, "name", type(inner).__name__))
        self._counters = counters
        self._timings = timings
        self._monotonic = monotonic

    def get_evidence(self, symbol: str, as_of: datetime | None = None):
        before = self._counters.request_total(self.name)
        self._counters.provider_calls[self.name] = (
            self._counters.provider_calls.get(self.name, 0) + 1
        )
        started = self._monotonic()
        try:
            result = self._inner.get_evidence(symbol, as_of)
        finally:
            elapsed = self._monotonic() - started
            self._timings.catalyst_seconds += elapsed
        after = self._counters.request_total(self.name)
        if self.name != "WEBULL_EARNINGS_SEC":
            if after > before:
                target = self._counters.cache_misses
            elif result.status in {CatalystStatus.TRUE, CatalystStatus.FALSE}:
                target = self._counters.cache_hits
            else:
                # A provider cooldown also avoids a request, but it is not a
                # cache hit and must remain explicitly indeterminate.
                target = self._counters.cache_indeterminate
            target[self.name] = target.get(self.name, 0) + 1
        return result


class CountingWebullDataClient:
    """Count sanitized namespace calls without exposing arguments or identity."""

    def __init__(self, inner: object, counters: RequestCounters) -> None:
        self._inner = inner
        self._counters = counters

    def __getattr__(self, name: str):
        value = getattr(self._inner, name)
        if name in {"market_data", "instrument", "screener", "fundamentals"}:
            return _CountingNamespace(value, name, self._counters)
        return value


class _CountingNamespace:
    def __init__(self, inner: object, namespace: str, counters: RequestCounters) -> None:
        self._inner = inner
        self._namespace = namespace
        self._counters = counters

    def __getattr__(self, name: str):
        value = getattr(self._inner, name)
        if not callable(value):
            return value

        def counted(*args, **kwargs):
            if self._namespace == "fundamentals" and name in {
                "get_earnings_calendar",
                "get_sec_filings",
            }:
                self._counters.webull_catalyst_requests += 1
            else:
                self._counters.webull_market_data_requests += 1
                self._counters.webull_market_data_rest_requests += 1
            return value(*args, **kwargs)

        return counted


class CountingCatalystTransport:
    def __init__(self, inner: object, provider: str, counters: RequestCounters) -> None:
        self._inner = inner
        self._provider = provider
        self._counters = counters

    def __getattr__(self, name: str):
        value = getattr(self._inner, name)
        if name not in {"fetch_news", "fetch_feed", "get"} or not callable(value):
            return value

        def counted(*args, **kwargs):
            if self._provider == "YAHOO_FINANCE":
                self._counters.yahoo_requests += 1
            elif self._provider == "CNBC":
                self._counters.cnbc_feed_requests += 1
            elif self._provider == "MARKETWATCH":
                self._counters.marketwatch_feed_requests += 1
            elif self._provider == "SEC_EDGAR":
                self._counters.sec_requests += 1
            return value(*args, **kwargs)

        return counted


class CountingStream:
    def __init__(self, inner: object, counters: RequestCounters) -> None:
        self._inner = inner
        self._counters = counters

    def connect(self) -> None:
        self._counters.webull_stream_connects += 1
        self._counters.webull_market_data_requests += 1
        self._inner.connect()

    def disconnect(self) -> None:
        self._inner.disconnect()

    def subscribe(self, channels) -> None:
        self._counters.webull_stream_subscriptions += 1
        self._counters.webull_market_data_requests += 1
        self._inner.subscribe(channels)

    def receive(self):
        receive = getattr(self._inner, "receive", None)
        if callable(receive):
            return receive()
        read_event = getattr(self._inner, "read_event", None)
        if callable(read_event):
            return read_event()
        raise TypeError("market-data stream has no receive boundary")

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class MarketDataOnlyAudit:
    """Minimal guard accepted by the stream builder; contains no trading identity."""

    def __init__(self, configuration) -> None:
        self._identity = RequestIdentity(
            RequestService.MARKET_DATA,
            configuration.environment.value,
            credential_fingerprint(configuration.api_key, configuration.api_secret),
        )

    def identity(self, service: RequestService) -> RequestIdentity:
        if service is not RequestService.MARKET_DATA:
            raise RuntimeError("scanner diagnostic supports market data only")
        return self._identity

    def record(self, identity: RequestIdentity, **_kwargs):
        if identity != self._identity:
            raise RuntimeError("unexpected market-data identity")
        return None


def _build_market_data_only_stream(
    configuration: OperationalConfiguration,
    market_configuration,
    audit: MarketDataOnlyAudit,
    *,
    clock: Clock,
) -> ReceiveTransportAdapter | None:
    """Build only the official Webull quote/snapshot/tick transport."""

    if not configuration.market_data_streaming_enabled:
        return None
    if (
        not market_configuration.api_key.strip()
        or not market_configuration.api_secret.strip()
    ):
        return None
    identity = audit.identity(RequestService.MARKET_DATA)
    audit.record(
        identity,
        endpoint=market_configuration.stream_url,
        capability_result="STREAM_CLIENT_REQUESTED",
    )
    credentials = WebullStreamingCredentials(
        app_key=market_configuration.api_key,
        app_secret=market_configuration.api_secret,
        session_id=f"scanner-diagnostic-{uuid4().hex}",
    )
    endpoint = select_official_sdk_stream_endpoint(
        market_configuration.stream_url
    )
    api_endpoint = urlparse(market_configuration.api_base_url)
    try:
        backend = create_official_stream_backend(
            credentials,
            create_official_market_subscription(clock=clock),
            receive_timeout_seconds=1.0,
            http_host=api_endpoint.hostname,
            mqtt_host=endpoint.mqtt_host,
            mqtt_port=endpoint.mqtt_port,
            tls_enable=endpoint.tls_enable,
            transport=endpoint.transport,
            websocket_path=endpoint.websocket_path,
        )
    except Exception:
        audit.record(
            identity,
            endpoint=market_configuration.stream_url,
            capability_result="STREAM_CLIENT_FAILED",
        )
        raise
    audit.record(
        identity,
        endpoint=market_configuration.stream_url,
        capability_result="STREAM_CLIENT_CREATED",
    )
    client = WebullWebSocketClient(
        backend,
        WebullMarketEventParser(clock=clock),
        ReconnectPolicy(
            maximum_attempts=configuration.stream_reconnect_attempts,
            backoff_seconds=configuration.stream_reconnect_backoff_seconds,
        ),
        lambda seconds: time_module.sleep(float(seconds)),
        StructuredLogger(_NullSink()),
    )
    return ReceiveTransportAdapter(client)


def _instrument_catalyst_providers(
    providers: tuple[object, ...],
    counters: RequestCounters,
    timings: DiagnosticTimings,
    *,
    monotonic: Monotonic = time_module.monotonic,
) -> tuple[ObservedCatalystProvider, ...]:
    observed: list[ObservedCatalystProvider] = []
    for provider in providers:
        name = str(getattr(provider, "name", type(provider).__name__))
        if name in {"YAHOO_FINANCE", "CNBC", "MARKETWATCH"}:
            transport = getattr(provider, "_transport", None)
            if transport is not None:
                setattr(
                    provider,
                    "_transport",
                    CountingCatalystTransport(transport, name, counters),
                )
        elif name == "SEC_EDGAR":
            client = getattr(provider, "_client", None)
            if client is not None:
                setattr(
                    provider,
                    "_client",
                    CountingCatalystTransport(client, name, counters),
                )
        observed.append(
            ObservedCatalystProvider(
                provider, counters, timings, monotonic=monotonic
            )
        )
    return tuple(observed)


def compose_production_runtime(
    limits: DiagnosticLimits,
    *,
    configuration_loader: Callable[[], OperationalConfiguration] = load_configuration,
    clock: Clock = lambda: datetime.now(UTC),
    monotonic: Monotonic = time_module.monotonic,
) -> DiagnosticRuntime:
    """Compose the real scanner without constructing any execution capability."""

    composition_started = monotonic()
    configuration = configuration_loader()
    if configuration.live_trading_enabled:
        raise RuntimeError(
            "scanner diagnostic requires LIVE_TRADING_ENABLED=false"
        )
    market_configuration = market_data_configuration(configuration)
    counters = RequestCounters()
    timings = DiagnosticTimings()
    lazy = LazyOfficialDataClient(
        lambda: CountingWebullDataClient(
            MarketDataClientFactory(market_configuration).create(), counters
        )
    )
    universe_provider = WebullScannerUniverseProvider(
        lazy,
        clock=clock,
        # Match the operational scanner's discovery breadth.  The independent
        # maximum-symbols limit bounds only warmup and subscriptions.
        page_size=50,
    )
    universe = BoundedUniverseSelector(
        UniverseService(universe_provider),
        limits.maximum_symbols,
        timings,
        monotonic=monotonic,
    )
    providers = build_catalyst_providers(lazy, configuration)
    observed_providers = _instrument_catalyst_providers(
        providers, counters, timings, monotonic=monotonic
    )
    recording_aggregator = RecordingCatalystAggregator(
        CatalystAggregator(observed_providers, clock=clock)
    )
    reference_provider = WebullScannerReferenceProvider(
        lazy,
        universe_provider,
        clock=clock,
        environment=market_configuration.environment.value,
        identity_scope=market_data_cache_scope(market_configuration)[1],
        catalyst_aggregator=recording_aggregator,
    )
    reference_service = TimedReferenceDataService(
        ReferenceDataService(
            reference_provider,
            cache=ReferenceDataCache(
                clock=clock,
                scope=market_data_cache_scope(market_configuration),
            ),
        ),
        timings,
        monotonic=monotonic,
    )
    references: dict[str, ReferenceRecord] = {}
    reference_store = ScannerReferenceStore()

    def store_reference(record: ReferenceRecord) -> None:
        references[record.symbol] = record
        reference_store.put(
            ScannerReferenceData(
                symbol=record.symbol,
                previous_close=record.previous_close,
                average_30_day_volume=record.average_30_day_volume,
                float_shares=record.float_shares,
                catalyst=record.catalyst,
                catalyst_headline=record.catalyst_headline,
                catalyst_status=record.catalyst_status,
                tradable=record.tradable,
                updated_at=record.as_of,
                current_volume=record.current_volume,
            )
        )

    stream = _build_market_data_only_stream(
        configuration,
        market_configuration,
        MarketDataOnlyAudit(market_configuration),
        clock=clock,
    )
    if stream is None:
        raise RuntimeError("Webull market-data streaming is disabled or unavailable")
    adapter = MarketEventScannerAdapter(reference_store)
    transport = ReceiveTransportAdapter(CountingStream(stream, counters))
    pipeline = MomentumScannerPipeline(adapter)
    engine = RealtimeScannerEngine(
        universe,
        reference_service,
        pipeline,
        reference_sink=store_reference,
        clock=clock,
    )
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        maximum_events_per_cycle=1,
    )
    infrastructure = DiagnosticScannerInfrastructure(
        transport=transport,
        pipeline=pipeline,
        engine=engine,
        coordinator=coordinator,
    )
    timings.configuration_composition_seconds = monotonic() - composition_started
    return DiagnosticRuntime(
        configuration=configuration,
        infrastructure=infrastructure,
        adapter=adapter,
        references=references,
        catalyst_results=recording_aggregator.results,
        counters=counters,
        timings=timings,
        universe=universe,
    )


def session_report(now: datetime) -> dict[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("diagnostic clock must return a timezone-aware datetime")
    eastern = now.astimezone(EASTERN)
    schedule = trading_day_schedule(eastern)
    return {
        "eastern_time": eastern.isoformat(),
        "session": market_session(eastern).value,
        "trading_day": schedule is not None,
        "market_open": _iso(None if schedule is None else schedule.market_open),
        "market_close": _iso(None if schedule is None else schedule.market_close),
        "early_close": False if schedule is None else schedule.is_early_close,
    }


def run_diagnostic(
    runtime: DiagnosticRuntime,
    limits: DiagnosticLimits,
    *,
    clock: Clock = lambda: datetime.now(UTC),
    monotonic: Monotonic = time_module.monotonic,
    event_sink: EventSink | None = None,
) -> dict[str, object]:
    """Run one bounded observation window and close before returning."""

    started = monotonic()
    startup = session_report(clock())
    if event_sink is not None:
        event_sink({"event": "scanner_diagnostic_startup", **startup})
    coordinator = runtime.infrastructure.coordinator
    primary_error: BaseException | None = None
    close_error: BaseException | None = None
    cycle_events = 0
    read_attempts = 0
    maximum_read_attempts = (
        limits.maximum_events + math.ceil(limits.duration_seconds) + 5
    )
    observation_started: float | None = None
    try:
        coordinator.start(asset_classes=(AssetClass.STOCK,))
        observation_started = monotonic()
        deadline = monotonic() + limits.duration_seconds
        while (
            monotonic() < deadline
            and cycle_events < limits.maximum_events
            and read_attempts < maximum_read_attempts
        ):
            cycle = coordinator.run_once()
            read_attempts += 1
            cycle_events += int(cycle.events_read)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        runtime.timings.stream_observation_seconds = (
            0.0
            if observation_started is None
            else monotonic() - observation_started
        )
        try:
            coordinator.close()
        except BaseException as exc:
            close_error = exc
            if primary_error is None:
                raise
        finally:
            runtime.timings.total_seconds = (
                runtime.timings.configuration_composition_seconds
                + monotonic()
                - started
            )

    completed_at = clock()
    completion = session_report(completed_at)
    snapshot = coordinator.snapshot(limit=limits.maximum_symbols)
    diagnostics = runtime.adapter.diagnostic_results(
        limit=limits.maximum_symbols
    )
    complete_rows: list[dict[str, object]] = []
    incomplete_rows: list[dict[str, object]] = []
    receiving_symbols: list[str] = []
    for item in diagnostics:
        receiving_symbols.append(item.state.symbol)
        if item.observation is None:
            incomplete_rows.append(
                {
                    "symbol": item.state.symbol,
                    "missing_fields": list(item.missing_fields),
                    "state": _partial_state(item),
                }
            )
            continue
        decision = next(
            (
                candidate
                for candidate in snapshot.decisions
                if candidate.symbol == item.state.symbol
            ),
            None,
        )
        if decision is None:
            incomplete_rows.append(
                {
                    "symbol": item.state.symbol,
                    "missing_fields": ["scanner_decision"],
                    "state": _partial_state(item),
                }
            )
            continue
        complete_rows.append(
            _complete_row(
                item,
                decision,
                startup,
                completed_at,
                runtime.references.get(item.state.symbol),
                runtime.catalyst_results.get(item.state.symbol),
            )
        )

    complete_rows.sort(key=lambda row: str(row["symbol"]))
    incomplete_rows.sort(key=lambda row: str(row["symbol"]))
    qualified = sum(bool(row["qualifies"]) for row in complete_rows)
    warmup = snapshot.warmup_result
    report = {
        "event": "scanner_diagnostic_complete",
        "safety": {
            "read_only": True,
            "storage_enabled": False,
            "execution_components_constructed": False,
            "live_trading_enabled": runtime.configuration.live_trading_enabled,
            "closed": not coordinator.status().connected,
            "cleanup_error": None if close_error is None else type(close_error).__name__,
        },
        "session": startup,
        "completion_session": completion,
        "limits": {
            "duration_seconds": limits.duration_seconds,
            "maximum_events": limits.maximum_events,
            "maximum_symbols": limits.maximum_symbols,
            "maximum_read_attempts": maximum_read_attempts,
        },
        "accounting": {
            "universe_discovered": runtime.universe.discovered_count,
            "universe_eligible": runtime.universe.eligible_count,
            "universe_selected_by_limit": runtime.universe.selected_count,
            "reference_warmup_success": len(warmup.successful_records),
            "reference_warmup_failure": len(warmup.failures),
            "subscribed_symbols": len(coordinator.channels),
            "symbols_receiving_events": len(set(receiving_symbols)),
            "symbols_with_partial_state": len(incomplete_rows),
            "symbols_with_complete_observations": len(complete_rows),
            "qualifying_candidates": qualified,
            "rejected_candidates": len(complete_rows) - qualified,
            "events_read": cycle_events,
            "read_attempts": read_attempts,
        },
        "reference_failures": [
            {
                "symbol": failure.symbol,
                "type": failure.failure_type,
                "stage": failure.stage,
                "retryable": failure.retryable,
            }
            for failure in warmup.failures
        ],
        "incomplete_observations": incomplete_rows,
        "complete_observations": complete_rows,
        "requests": runtime.counters.as_dict(),
    }
    runtime.timings.total_seconds = (
        runtime.timings.configuration_composition_seconds
        + monotonic()
        - started
    )
    report["performance"] = {
        **runtime.timings.as_dict(),
        "serialized_per_symbol_reference_warmup": True,
        "serialized_per_symbol_catalyst_evaluation": True,
    }
    return report


def _complete_row(
    result: AdapterResult,
    decision,
    startup: Mapping[str, object],
    reported_at: datetime,
    reference: ReferenceRecord | None,
    catalyst: CatalystAggregationResult | None,
) -> dict[str, object]:
    observation = result.observation
    assert observation is not None
    state = result.state
    selected = None if catalyst is None else catalyst.selected
    sources: list[str] = []
    if catalyst is not None and selected is not None:
        event = next(
            (
                item
                for item in catalyst.events
                if item.identity == selected.event_identity
            ),
            None,
        )
        if event is not None:
            sources = list(event.sources)
    current_session = MarketSession(str(startup["session"]))
    observation_session = market_session(observation.timestamp)
    schedule = trading_day_schedule(reported_at.astimezone(EASTERN))
    price_timestamp = _latest(
        state.trade_timestamp,
        state.snapshot_timestamp,
    )
    quote_timestamp = state.quote_timestamp
    metrics = decision.metrics
    field_validity = {
        "current_price": observation.price > 0 and price_timestamp is not None,
        "percentage_change_gap": metrics.percentage_change
        == (observation.price - observation.previous_close)
        / observation.previous_close
        * Decimal("100"),
        "previous_close_reference": observation.previous_close > 0,
        "volume": observation.current_volume > 0,
        "relative_volume": metrics.relative_volume
        == observation.current_volume / observation.average_30_day_volume,
        "float": observation.float_shares is not None
        and observation.float_shares > 0,
        "dollar_volume": metrics.dollar_volume
        == observation.price * observation.current_volume,
        "bid_ask_spread": observation.bid is not None
        and observation.ask is not None
        and observation.ask >= observation.bid
        and metrics.spread_percent is not None,
        "catalyst_status": isinstance(observation.catalyst_status, CatalystStatus),
    }
    session_checks = _session_checks(
        current_session,
        schedule,
        price_timestamp,
        quote_timestamp,
        observation.current_volume,
        None if reference is None else reference.current_volume,
        bool(
            field_validity["previous_close_reference"]
            and field_validity["percentage_change_gap"]
        ),
    )
    return {
        "symbol": decision.symbol,
        "session": current_session.value,
        "observation_session": observation_session.value,
        "price": str(observation.price),
        "previous_close": str(observation.previous_close),
        "percentage_change_gap": str(metrics.percentage_change),
        "relative_volume": str(metrics.relative_volume),
        "volume": str(observation.current_volume),
        "float": str(observation.float_shares),
        "dollar_volume": str(metrics.dollar_volume),
        "bid": str(observation.bid),
        "ask": str(observation.ask),
        "spread_percent": str(metrics.spread_percent),
        "catalyst_status": observation.catalyst_status.value,
        "catalyst_type": observation.catalyst.value,
        "selected_catalyst_source": None if selected is None else selected.source,
        "selected_headline": (
            None
            if not decision.qualified or selected is None
            else selected.headline
        ),
        "corroborating_sources": sources if decision.qualified else [],
        "score": decision.score,
        "qualifies": decision.qualified,
        "failed_conditions": list(decision.failed_rules),
        "passed_conditions": list(decision.passed_rules),
        "observed_at": _iso(observation.timestamp),
        "price_observed_at": _iso(price_timestamp),
        "quote_observed_at": _iso(quote_timestamp),
        "reference_observed_at": _iso(None if reference is None else reference.as_of),
        "freshness_seconds": {
            "price": _age_seconds(reported_at, price_timestamp),
            "quote": _age_seconds(reported_at, quote_timestamp),
            "reference": _age_seconds(
                reported_at, None if reference is None else reference.as_of
            ),
        },
        "field_validity": field_validity,
        "session_checks": {
            **session_checks,
            "matches_startup_session": (
                observation_session is current_session
            ),
        },
    }


def _session_checks(
    session: MarketSession,
    schedule: TradingDaySchedule | None,
    price_timestamp: datetime | None,
    quote_timestamp: datetime | None,
    volume: Decimal,
    discovery_volume: Decimal | None,
    previous_close_change_valid: bool,
) -> dict[str, object]:
    price_in_session = _timestamp_in_session(price_timestamp, session, schedule)
    quote_in_session = _timestamp_in_session(quote_timestamp, session, schedule)
    result: dict[str, object] = {
        "price_timestamp_in_current_session": price_in_session,
        "quote_timestamp_in_current_session": quote_in_session,
        "change_basis": "WEBULL_PREVIOUS_CLOSE",
        "discovery_volume_preserved": (
            None if discovery_volume is None else volume >= discovery_volume
        ),
    }
    if session is MarketSession.PREMARKET:
        result["premarket_previous_close_basis_valid"] = (
            previous_close_change_valid
        )
        result["premarket_activity_represented"] = (
            price_in_session
            and quote_in_session
            and (discovery_volume is None or volume >= discovery_volume)
        )
    if session is MarketSession.AFTER_HOURS:
        result["extended_hours_price_not_core_stale"] = price_in_session
        result["extended_hours_quote_not_core_stale"] = quote_in_session
    if session is MarketSession.CORE:
        result["core_price_current"] = price_in_session
        result["core_quote_current"] = quote_in_session
    return result


def _timestamp_in_session(
    value: datetime | None,
    session: MarketSession,
    schedule: TradingDaySchedule | None,
) -> bool:
    if value is None or schedule is None:
        return False
    current = value.astimezone(EASTERN)
    current_time = current.time()
    if session is MarketSession.PREMARKET:
        return time(4) <= current_time < schedule.market_open.time()
    if session is MarketSession.CORE:
        return schedule.market_open <= current < schedule.market_close
    if session is MarketSession.AFTER_HOURS:
        return schedule.market_close <= current and current_time < time(20)
    if session is MarketSession.OVERNIGHT:
        return current_time < time(4) or current_time >= time(20)
    return False


def _partial_state(result: AdapterResult) -> dict[str, object]:
    state = result.state
    return {
        "last_price": _decimal(state.last_price),
        "bid": _decimal(state.bid),
        "ask": _decimal(state.ask),
        "current_volume": str(state.cumulative_volume),
        "last_event_at": _iso(state.timestamp),
        "quote_at": _iso(state.quote_timestamp),
        "trade_at": _iso(state.trade_timestamp),
        "snapshot_at": _iso(state.snapshot_timestamp),
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _latest(*values: datetime | None) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    return None if value is None else (now - value).total_seconds()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--maximum-events", type=int, default=1_000)
    parser.add_argument("--maximum-symbols", type=int, default=25)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional explicit JSON report path; no file is written by default",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        limits = DiagnosticLimits(
            duration_seconds=arguments.duration_seconds,
            maximum_events=arguments.maximum_events,
            maximum_symbols=arguments.maximum_symbols,
        )
        def emit(value: Mapping[str, object]) -> None:
            print(json.dumps(value, sort_keys=True), flush=True)

        runtime = compose_production_runtime(limits)
        report = run_diagnostic(runtime, limits, event_sink=emit)
        serialized = json.dumps(report, sort_keys=True)
        print(serialized, flush=True)
        if arguments.output is not None:
            arguments.output.write_text(serialized + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "scanner_diagnostic_failed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
