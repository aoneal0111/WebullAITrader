from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.catalysts.models import (
    CatalystAggregationResult,
    CatalystEvent,
    CatalystEvidence,
)
from app.configuration import load_configuration
from app.live_scanner.models import LiveScannerCycle, LiveScannerStatus
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)
from app.momentum_scanner import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    rank_candidates,
    MomentumScannerConfig,
)
from app.reference_data.models import ReferenceRecord
from app.realtime_scanner.models import ReferenceWarmupResult, ScannerSnapshot
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    MomentumScannerPipeline,
    ScannerReferenceData,
    ScannerReferenceStore,
)
from app.scanner_diagnostic import (
    CountingCatalystTransport,
    CountingStream,
    CountingWebullDataClient,
    DiagnosticLimits,
    DiagnosticRuntime,
    DiagnosticTimings,
    ObservedCatalystProvider,
    RecordingCatalystAggregator,
    RequestCounters,
    _instrument_catalyst_providers,
    compose_production_runtime,
    run_diagnostic,
    session_report,
)
from app import scanner_diagnostic


PREMARKET = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
CORE = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
AFTER_HOURS = datetime(2026, 7, 20, 21, 0, tzinfo=UTC)
OVERNIGHT = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
CLOSED = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)


class FakeCoordinator:
    def __init__(self, pipeline, adapter, events, reference, *, fail=False):
        self.pipeline = pipeline
        self.adapter = adapter
        self.events = list(events)
        self.reference = reference
        self.fail = fail
        self.started = False
        self.closed = False
        self.connected = False
        self.run_calls = 0
        self.decisions = {}
        self.channels = (reference.symbol,)

    def start(self, *, asset_classes):
        assert asset_classes == (AssetClass.STOCK,)
        self.started = True
        self.connected = True
        return self.channels

    def run_once(self):
        self.run_calls += 1
        if self.fail:
            raise RuntimeError("synthetic receive failure")
        if not self.events:
            return LiveScannerCycle(0, 0, True, True)
        event = self.events.pop(0)
        decision = self.pipeline.consume(event)
        if decision is not None:
            self.decisions[decision.symbol] = decision
        return LiveScannerCycle(1, int(decision is not None), False, True)

    def snapshot(self, *, limit=25):
        decisions = tuple(self.decisions[s] for s in sorted(self.decisions))
        return ScannerSnapshot(
            timestamp=CORE,
            active_symbols=self.channels,
            decisions=decisions,
            ranked_candidates=rank_candidates(decisions, limit=limit),
            processed_events=self.run_calls,
            ignored_events=0,
            reference_failures=(),
            warmup_result=ReferenceWarmupResult(
                active_symbols=self.channels,
                successful_records=(self.reference,),
            ),
            universe_size=1,
            eligible_symbol_count=1,
        )

    def close(self):
        self.closed = True
        self.connected = False

    def status(self):
        return LiveScannerStatus(
            connected=self.connected,
            running=self.started and not self.closed,
            channels=self.channels,
            cycles_completed=self.run_calls,
            events_read=0,
            decisions_created=len(self.decisions),
        )


class FrozenMonotonic:
    def __call__(self):
        return 0.0


class StepMonotonic:
    def __init__(self, step=0.01):
        self.value = 0.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


def reference(*, catalyst=True, volume=Decimal("6000000")):
    return ReferenceRecord(
        symbol="TEST",
        asset_class=AssetClass.STOCK,
        exchange="NASDAQ",
        previous_close=Decimal("10"),
        average_30_day_volume=Decimal("1000000"),
        float_shares=Decimal("5000000"),
        market_cap=Decimal("60000000"),
        shares_outstanding=Decimal("5000000"),
        tradable=True,
        catalyst=CatalystType.EARNINGS if catalyst else CatalystType.NONE,
        catalyst_headline="Test reports earnings" if catalyst else None,
        catalyst_status=(CatalystStatus.TRUE if catalyst else CatalystStatus.FALSE),
        as_of=CORE,
        current_volume=volume,
    )


def catalyst_result(*, positive=True):
    item = CatalystEvidence(
        symbol="TEST",
        catalyst_type=(CatalystType.EARNINGS if positive else CatalystType.NONE),
        status=(CatalystStatus.TRUE if positive else CatalystStatus.FALSE),
        headline="Test reports earnings" if positive else None,
        source="YAHOO_FINANCE",
        published_at=CORE if positive else None,
        source_url="https://example.test/story" if positive else None,
        provider_event_id="story-1" if positive else None,
    )
    events = (
        CatalystEvent(
            identity=item.event_identity,
            symbol="TEST",
            catalyst_type=item.catalyst_type,
            evidence=(item,),
        ),
    ) if positive else ()
    return CatalystAggregationResult(item, events, (item,))


def events(at):
    return (
        MarketEvent(
            1,
            at,
            "TEST",
            "WEBULL",
            MarketEventType.QUOTE,
            QuotePayload(
                bid=Decimal("11.99"),
                ask=Decimal("12.01"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100"),
            ),
        ),
        MarketEvent(
            2,
            at,
            "TEST",
            "WEBULL",
            MarketEventType.TRADE,
            TradePayload(
                price=Decimal("12"),
                size=Decimal("6000000"),
                trade_id="snapshot-test",
            ),
        ),
    )


def runtime(
    at, *, positive=True, supplied_events=None, fail=False,
    scanner_config: MomentumScannerConfig = MomentumScannerConfig(),
):
    record = reference(catalyst=positive)
    store = ScannerReferenceStore()
    store.put(
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
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(adapter, scanner_config)
    coordinator = FakeCoordinator(
        pipeline,
        adapter,
        events(at) if supplied_events is None else supplied_events,
        record,
        fail=fail,
    )
    configuration = load_configuration(
        {
            "LIVE_TRADING_ENABLED": "false",
            "MARKET_DATA_STREAMING_ENABLED": "true",
        }
    )
    return DiagnosticRuntime(
        configuration=configuration,
        infrastructure=SimpleNamespace(coordinator=coordinator),
        adapter=adapter,
        references={"TEST": record},
        catalyst_results={"TEST": catalyst_result(positive=positive)},
        counters=RequestCounters(),
        timings=DiagnosticTimings(),
        universe=SimpleNamespace(
            discovered_count=1,
            eligible_count=1,
            selected_count=1,
        ),
    )


def execute(
    at, *, positive=True, supplied_events=None,
    scanner_config: MomentumScannerConfig = MomentumScannerConfig(),
):
    value = runtime(
        at,
        positive=positive,
        supplied_events=supplied_events,
        scanner_config=scanner_config,
    )
    event_count = len(events(at) if supplied_events is None else supplied_events)
    report = run_diagnostic(
        value,
        DiagnosticLimits(60, max(1, event_count), 1),
        clock=lambda: at,
        monotonic=StepMonotonic(),
    )
    return value, report


def test_bounded_shutdown_even_when_transport_never_yields() -> None:
    value = runtime(CORE, supplied_events=())
    report = run_diagnostic(
        value,
        DiagnosticLimits(1, 1, 1),
        clock=lambda: CORE,
        monotonic=FrozenMonotonic(),
    )

    assert value.infrastructure.coordinator.run_calls == 7
    assert value.infrastructure.coordinator.closed is True
    assert report["safety"]["closed"] is True


def test_module_has_no_execution_component_construction() -> None:
    tree = ast.parse(inspect.getsource(scanner_diagnostic))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert calls.isdisjoint(
        {
            "create_broker_runtime",
            "build_webull_broker",
            "ExecutionCoordinator",
            "OrderCoordinator",
            "RiskReservation",
        }
    )


def test_production_composition_refuses_live_trading_before_network() -> None:
    configuration = load_configuration(
        {
            "LIVE_TRADING_ENABLED": "true",
            "MARKET_DATA_STREAMING_ENABLED": "true",
        }
    )
    with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED=false"):
        compose_production_runtime(
            DiagnosticLimits(1, 1, 1),
            configuration_loader=lambda: configuration,
        )


def test_run_diagnostic_does_not_write_persistent_storage(monkeypatch) -> None:
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *args, **kwargs: pytest.fail("unexpected storage write"),
    )
    _value, report = execute(CORE)
    assert report["safety"]["storage_enabled"] is False


@pytest.mark.parametrize(
    ("at", "expected"),
    (
        (PREMARKET, "PREMARKET"),
        (CORE, "CORE"),
        (AFTER_HOURS, "AFTER_HOURS"),
    ),
)
def test_session_reporting(at, expected) -> None:
    report = session_report(at)
    assert report["session"] == expected
    assert report["trading_day"] is True
    assert report["market_open"] is not None
    assert report["market_close"] is not None
    assert report["early_close"] is False


@pytest.mark.parametrize(
    ("at", "expected", "trading_day"),
    (
        (OVERNIGHT, "OVERNIGHT", True),
        (CLOSED, "CLOSED", False),
    ),
)
def test_overnight_and_closed_are_explicit(at, expected, trading_day) -> None:
    report = session_report(at)
    assert report["session"] == expected
    assert report["trading_day"] is trading_day


def test_incomplete_state_reports_each_missing_field() -> None:
    partial = events(CORE)[:1]
    _value, report = execute(CORE, supplied_events=partial)
    assert report["accounting"]["symbols_with_partial_state"] == 1
    assert report["accounting"]["symbols_with_complete_observations"] == 0
    assert report["incomplete_observations"] == [
        {
            "symbol": "TEST",
            "missing_fields": ["last_price"],
            "state": {
                "last_price": None,
                "bid": "11.99",
                "ask": "12.01",
                "current_volume": "6000000",
                "last_event_at": CORE.isoformat(),
                "quote_at": CORE.isoformat(),
                "trade_at": None,
                "snapshot_at": None,
            },
        }
    ]


def test_complete_observation_and_candidate_report() -> None:
    value, report = execute(CORE)
    row = report["complete_observations"][0]
    assert value.infrastructure.coordinator.closed is True
    assert report["accounting"]["symbols_with_complete_observations"] == 1
    assert report["accounting"]["qualifying_candidates"] == 1
    assert row["symbol"] == "TEST"
    assert row["price"] == "12"
    assert row["percentage_change_gap"] == "20.0"
    assert row["relative_volume"] == "6"
    assert row["volume"] == "6000000"
    assert row["selected_catalyst_source"] == "YAHOO_FINANCE"
    assert row["selected_headline"] == "Test reports earnings"
    assert row["corroborating_sources"] == ["YAHOO_FINANCE"]
    assert row["qualifies"] is True
    assert row["failed_conditions"] == []
    assert all(row["field_validity"].values())


def test_zero_candidate_run_keeps_thresholds_and_explanations() -> None:
    _value, report = execute(
        CORE, positive=False,
        scanner_config=MomentumScannerConfig.conservative_v1(),
    )
    row = report["complete_observations"][0]
    assert report["accounting"]["qualifying_candidates"] == 0
    assert report["accounting"]["rejected_candidates"] == 1
    assert row["qualifies"] is False
    assert row["failed_conditions"] == ["news_catalyst"]
    assert row["selected_headline"] is None


@pytest.mark.parametrize(
    ("at", "check"),
    (
        (PREMARKET, "premarket_activity_represented"),
        (CORE, "core_price_current"),
        (AFTER_HOURS, "extended_hours_price_not_core_stale"),
    ),
)
def test_complete_observation_validates_current_session(at, check) -> None:
    _value, report = execute(at)
    row = report["complete_observations"][0]
    assert row["session"] == session_report(at)["session"]
    assert row["session_checks"][check] is True
    assert row["session_checks"]["price_timestamp_in_current_session"] is True
    assert row["session_checks"]["quote_timestamp_in_current_session"] is True


def test_after_hours_flags_core_only_values_as_stale() -> None:
    _value, report = execute(AFTER_HOURS, supplied_events=events(CORE))
    checks = report["complete_observations"][0]["session_checks"]
    assert checks["extended_hours_price_not_core_stale"] is False
    assert checks["extended_hours_quote_not_core_stale"] is False


class FakeNewsTransport:
    def __init__(self):
        self.calls = 0

    def fetch_news(self, symbol):
        self.calls += 1
        return {}


class CachingProvider:
    name = "YAHOO_FINANCE"

    def __init__(self):
        self._transport = FakeNewsTransport()
        self._cached = False

    def get_evidence(self, symbol, as_of=None):
        if not self._cached:
            self._transport.fetch_news(symbol)
            self._cached = True
        return CatalystEvidence(
            symbol=symbol,
            catalyst_type=CatalystType.NONE,
            status=CatalystStatus.FALSE,
            source=self.name,
        )


def test_provider_request_counters_and_cache_observation() -> None:
    counters = RequestCounters()
    timings = DiagnosticTimings()
    provider = CachingProvider()
    observed = _instrument_catalyst_providers(
        (provider,), counters, timings, monotonic=StepMonotonic()
    )[0]
    observed.get_evidence("TEST", CORE)
    observed.get_evidence("TEST", CORE)

    assert counters.yahoo_requests == 1
    assert counters.provider_calls == {"YAHOO_FINANCE": 2}
    assert counters.cache_misses == {"YAHOO_FINANCE": 1}
    assert counters.cache_hits == {"YAHOO_FINANCE": 1}
    assert counters.as_dict()["snapshot_based"] == {
        "CNBC": True,
        "MARKETWATCH": True,
    }


def test_webull_request_counter_separates_market_data_and_catalysts() -> None:
    class Namespace:
        def call(self):
            return None

        def get_earnings_calendar(self):
            return None

    counters = RequestCounters()
    client = CountingWebullDataClient(
        SimpleNamespace(
            market_data=Namespace(),
            fundamentals=Namespace(),
        ),
        counters,
    )
    client.market_data.call()
    client.fundamentals.get_earnings_calendar()
    assert counters.webull_market_data_requests == 1
    assert counters.webull_market_data_rest_requests == 1
    assert counters.webull_catalyst_requests == 1


def test_counting_stream_accepts_existing_read_event_adapter_boundary() -> None:
    class Stream:
        def connect(self):
            pass

        def disconnect(self):
            pass

        def subscribe(self, channels):
            self.channels = channels

        def read_event(self):
            return "event"

    counters = RequestCounters()
    stream = CountingStream(Stream(), counters)
    stream.connect()
    stream.subscribe(("TEST",))
    assert stream.receive() == "event"
    assert counters.webull_market_data_requests == 2
    assert counters.webull_stream_connects == 1
    assert counters.webull_stream_subscriptions == 1


def test_cleanup_after_exception() -> None:
    value = runtime(CORE, fail=True)
    with pytest.raises(RuntimeError, match="synthetic receive failure"):
        run_diagnostic(
            value,
            DiagnosticLimits(5, 5, 1),
            clock=lambda: CORE,
            monotonic=StepMonotonic(),
        )
    assert value.infrastructure.coordinator.closed is True

def test_recording_catalyst_aggregator_preserves_aggregate_result_contract() -> None:
    selected = object()
    result = SimpleNamespace(selected=selected)

    class Inner:
        def __init__(self) -> None:
            self.calls = []

        def aggregate_result(self, symbol, as_of=None):
            self.calls.append((symbol, as_of))
            return result

    inner = Inner()
    recorder = RecordingCatalystAggregator(inner)

    returned = recorder.aggregate_result("test", CORE)

    assert returned is result
    assert recorder.results == {"TEST": result}
    assert inner.calls == [("test", CORE)]

    evidence = recorder.get_evidence("TEST", CORE)

    assert evidence is selected
    assert recorder.results == {"TEST": result}
    assert inner.calls == [
        ("test", CORE),
        ("TEST", CORE),
    ]
