from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Lock, Thread
from time import monotonic, sleep

from app.live_scanner import LiveScannerCoordinator
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)
from app.momentum_scanner import AssetClass, CatalystType
from app.performance_diagnostics import performance_diagnostics
from app.realtime_scanner import RealtimeScannerEngine
from app.reference_data import ReferenceRecord
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    MomentumScannerPipeline,
    ScannerReferenceData,
    ScannerReferenceStore,
)
from app.universe import SecurityType, UniverseSelection, UniverseSymbol


NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
D = Decimal


def _symbol(symbol: str) -> UniverseSymbol:
    return UniverseSymbol(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        exchange="NASDAQ",
        security_type=SecurityType.COMMON_STOCK,
        tradable=True,
        price=D("5"),
        average_30_day_volume=D("1000000"),
        quote_currency="USD",
    )


def _record(symbol: str, *, previous_close: str = "4") -> ReferenceRecord:
    return ReferenceRecord(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        exchange="NASDAQ",
        previous_close=D(previous_close),
        average_30_day_volume=D("1000000"),
        float_shares=D("8000000"),
        market_cap=None,
        shares_outstanding=None,
        tradable=True,
        catalyst=CatalystType.EARNINGS,
        catalyst_headline="Test catalyst",
        as_of=NOW,
    )


class _Universe:
    def __init__(self, symbols: tuple[UniverseSymbol, ...]) -> None:
        self.symbols = symbols

    def select_all(self, asset_classes=()) -> UniverseSelection:
        allowed = set(asset_classes)
        return UniverseSelection(
            included=tuple(
                item for item in self.symbols
                if not allowed or item.asset_class in allowed
            ),
            excluded=(),
        )


class _Pipeline:
    def __init__(self) -> None:
        self.ready: list[str] = []

    def reference_ready(self, symbol: str) -> bool:
        self.ready.append(symbol)
        return True

    def consume(self, event):
        return None


class _Transport:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, ...]] = []

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def subscribe(self, channels: tuple[str, ...]) -> None:
        self.subscriptions.append(channels)

    def read_event(self):
        return None


class _BlockingReferences:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls: list[str] = []
        self._active = 0
        self.maximum_concurrent = 0
        self._lock = Lock()

    def get(self, symbol, asset_class=AssetClass.STOCK, *, force_refresh=False):
        with self._lock:
            self.calls.append(symbol)
            self._active += 1
            self.maximum_concurrent = max(self.maximum_concurrent, self._active)
        try:
            self.entered.set()
            assert self.release.wait(3.0)
            return _record(symbol)
        finally:
            with self._lock:
                self._active -= 1


def _engine(symbols, references, *, pipeline=None, sink=None):
    return RealtimeScannerEngine(
        _Universe(tuple(symbols)),
        references,
        pipeline or _Pipeline(),
        reference_sink=sink,
        clock=lambda: NOW,
    )


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.005)
    return predicate()


def test_initial_and_periodic_refresh_share_one_single_flight() -> None:
    references = _BlockingReferences()
    engine = _engine((_symbol("AAA"), _symbol("AAA"), _symbol("BBB")), references)
    coordinator = LiveScannerCoordinator(
        _Transport(), engine, universe_refresh_interval_seconds=0.01,
    )

    coordinator.start(asset_classes=(AssetClass.STOCK,))
    assert references.entered.wait(1.0)
    assert _wait_until(
        lambda: engine.reference_refresh_metrics().refresh_skipped_due_to_inflight > 0
    )

    running = engine.reference_refresh_metrics()
    assert running.generation == 1
    assert running.symbols_unique == 2
    assert running.symbols_duplicate_suppressed == 1
    assert references.calls == ["AAA"]
    assert references.maximum_concurrent == 1

    references.release.set()
    assert _wait_until(lambda: engine.reference_refresh_metrics().generation >= 2)
    coordinator.stop()

    assert references.maximum_concurrent == 1
    assert engine.reference_refresh_metrics().state == "IDLE"
    assert performance_diagnostics.startup_metrics()[
        "reference_warmup_symbols_total"
    ] == 2


def test_duplicate_discovery_membership_is_one_request_per_generation() -> None:
    references = _BlockingReferences()
    references.release.set()
    engine = _engine(
        (_symbol("AAA"), _symbol("AAA"), _symbol("BBB"), _symbol("BBB")),
        references,
    )

    engine.refresh_universe((AssetClass.STOCK,))

    assert references.calls == ["AAA", "BBB"]
    metrics = engine.reference_refresh_metrics()
    assert metrics.symbols_unique == 2
    assert metrics.symbols_completed == 2
    assert metrics.symbols_duplicate_suppressed == 2


class _VersionedReferences:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    def get(self, symbol, asset_class=AssetClass.STOCK, *, force_refresh=False):
        self.calls += 1
        call = self.calls
        if call == 1:
            self.entered.set()
            assert self.release.wait(3.0)
        return _record(symbol, previous_close=str(3 + call))


def test_stopped_generation_cannot_publish_after_late_completion() -> None:
    references = _VersionedReferences()
    pipeline = _Pipeline()
    stored: list[ReferenceRecord] = []
    engine = _engine((_symbol("AAA"),), references, pipeline=pipeline, sink=stored.append)
    worker = Thread(
        target=engine.refresh_universe,
        args=((AssetClass.STOCK,),),
        daemon=True,
    )
    worker.start()
    assert references.entered.wait(1.0)

    engine.stop_reference_refresh()
    references.release.set()
    worker.join(1.0)
    assert not worker.is_alive()
    assert stored == []
    assert pipeline.ready == []

    engine.refresh_universe((AssetClass.STOCK,))

    assert [item.previous_close for item in stored] == [D("5")]
    assert pipeline.ready == ["AAA"]
    assert engine.reference_refresh_metrics().generation == 2


def _scanner_reference() -> ScannerReferenceData:
    return ScannerReferenceData(
        symbol="TEST",
        previous_close=D("5"),
        average_30_day_volume=D("100000"),
        float_shares=D("5000000"),
        catalyst=CatalystType.NONE,
        tradable=True,
        updated_at=NOW,
        current_volume=D("100"),
    )


def _market_events(received_at: datetime) -> tuple[MarketEvent, MarketEvent]:
    return (
        MarketEvent(
            1, NOW, "TEST", "WEBULL", MarketEventType.QUOTE,
            QuotePayload(D("5.99"), D("6.01"), D("100"), D("100")),
            received_timestamp=received_at,
        ),
        MarketEvent(
            2, NOW, "TEST", "WEBULL", MarketEventType.TRADE,
            TradePayload(D("6"), D("900000"), "2"),
            received_timestamp=received_at,
        ),
    )


def test_reference_ready_uses_new_admission_age_but_preserves_market_age(
    monkeypatch,
) -> None:
    late = NOW + timedelta(seconds=120)
    clock = lambda: late
    ages: list[float] = []
    components: list[tuple[str, float]] = []
    monkeypatch.setattr(
        performance_diagnostics,
        "record_event_processing_age",
        lambda duration_ms: ages.append(duration_ms),
    )
    monkeypatch.setattr(
        performance_diagnostics,
        "record_component_duration",
        lambda name, duration_ms, **kwargs: components.append((name, duration_ms)),
    )

    store = ScannerReferenceStore()
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(
        adapter, clock=clock, coalesce_observational=True,
    )
    quote, trade = _market_events(NOW)
    pipeline.consume(quote)
    pipeline.consume(trade)
    store.put(_scanner_reference())

    assert pipeline.reference_ready("TEST") is True
    decisions = pipeline.drain_evaluations()

    assert len(decisions) == 1
    assert pipeline._latest_events["TEST"] is trade
    assert trade.timestamp == NOW
    assert trade.received_timestamp == NOW
    assert ages == [0.0]
    by_name = dict(components)
    assert by_name["scanner.retained_event_source_age"] == 120_000.0
    assert by_name["scanner.reevaluation_mailbox_age"] == 0.0
    population = pipeline.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=late,
    )
    assert population["missing_state_counts"]["STALE_LIVE_DATA"] == 1

    baseline_store = ScannerReferenceStore()
    baseline = MomentumScannerPipeline(
        MarketEventScannerAdapter(baseline_store),
        clock=clock,
        coalesce_observational=True,
    )
    baseline.consume(quote)
    baseline.consume(trade)
    baseline_store.put(_scanner_reference())
    baseline_result = baseline.adapter.reference_updated("TEST")
    assert baseline_result is not None
    expected_decision = baseline._evaluate_result(
        trade, baseline_result, publish=False,
    )
    assert expected_decision is not None
    actual_decision = decisions[0]
    assert actual_decision.symbol == expected_decision.symbol
    assert actual_decision.qualified == expected_decision.qualified
    assert actual_decision.score == expected_decision.score
    assert actual_decision.metrics == expected_decision.metrics
    assert actual_decision.passed_rules == expected_decision.passed_rules
    assert actual_decision.failed_rules == expected_decision.failed_rules


def test_shutdown_invalidates_refresh_and_returns_within_join_bound() -> None:
    references = _BlockingReferences()
    engine = _engine(tuple(_symbol(f"S{index:03d}") for index in range(300)), references)
    coordinator = LiveScannerCoordinator(
        _Transport(), engine, universe_refresh_interval_seconds=0.01,
    )
    coordinator.start(asset_classes=(AssetClass.STOCK,))
    assert references.entered.wait(1.0)
    releaser = Thread(target=lambda: (sleep(0.05), references.release.set()), daemon=True)
    releaser.start()

    started = monotonic()
    coordinator.stop()
    elapsed = monotonic() - started

    releaser.join(1.0)
    assert elapsed < 1.0
    assert engine.reference_refresh_metrics().state == "IDLE"
    assert references.calls == ["S000"]
