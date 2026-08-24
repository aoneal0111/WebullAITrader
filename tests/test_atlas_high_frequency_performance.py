from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections import deque
from decimal import Decimal
from itertools import count
from threading import Event, get_ident

from PySide6.QtWidgets import QApplication

from app.gui.state_bridge import QtStateBridge
from app.live_scanner import LiveScannerCoordinator
from app.market_data.models import (
    BookLevel,
    MarketEvent,
    MarketEventType,
    OrderBookSnapshotPayload,
    QuotePayload,
    TradePayload,
)
from app.momentum_scanner import AssetClass
from app.momentum_scanner import CatalystType, ScannerDecision, ScannerMetrics
from app.operations.runtime import PaperRuntimeEvent
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.operations_core import ApplicationStateStore, OperationsBus, RuntimeCycleCompleted
from app.performance_diagnostics import PerformanceDiagnostics
from app.read_models.timeline import TimelineReadModelSnapshot
from app.read_models.timeline_projection import TimelineProjection
from app.realtime_scanner import ScannerSnapshot
from app.services import RuntimeService


NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)


def _snapshot(*candidates: ScannerDecision, processed: int = 0) -> ScannerSnapshot:
    return ScannerSnapshot(
        timestamp=NOW,
        active_symbols=tuple(candidate.symbol for candidate in candidates),
        decisions=tuple(candidates),
        ranked_candidates=tuple(candidates),
        processed_events=processed,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )


def _candidate(symbol: str = "S00", *, score: int = 90) -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol,
        qualified=True,
        score=score,
        metrics=ScannerMetrics(
            percentage_change=Decimal("12"),
            relative_volume=Decimal("6"),
            dollar_volume=Decimal("5000000"),
            spread_percent=Decimal("0.2"),
        ),
        passed_rules=("price_range",),
        failed_rules=(),
        timestamp=NOW,
        price=Decimal("5"),
        current_volume=Decimal("1000000"),
        catalyst=CatalystType.OTHER,
    )


def test_30k_state_updates_coalesce_to_one_bounded_gui_refresh() -> None:
    application = QApplication.instance() or QApplication([])
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    diagnostics = PerformanceDiagnostics()
    bridge = QtStateBridge(
        store,
        refresh_interval_ms=125,
        diagnostics=diagnostics,
    )
    rendered = []
    bridge.state_changed.connect(rendered.append)

    try:
        for cycle in range(1, 30_001):
            bus.publish(RuntimeCycleCompleted(cycle_count=cycle))

        assert rendered == []
        assert diagnostics.snapshot().pending_gui_updates == 1
        assert diagnostics.snapshot().maximum_pending_gui_updates == 1

        # Deterministically execute the same timer boundary without waiting on
        # wall-clock scheduling in the test runner.
        bridge._flush()
        application.processEvents()

        metrics = diagnostics.snapshot()
        assert len(rendered) == 1
        assert rendered[0].runtime.cycles_completed == 30_000
        assert metrics.gui_refresh_count == 1
        assert metrics.pending_gui_updates == 0
    finally:
        bridge.close()
        store.close()


def test_identical_zero_and_candidate_snapshots_publish_only_on_change() -> None:
    events = []
    diagnostics = PerformanceDiagnostics()
    sequences = count(1)
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: next(sequences),
        source="load-test",
        stale_after=timedelta(seconds=30),
        diagnostics=diagnostics,
    )

    for processed in range(30_000):
        publisher.publish(_snapshot(processed=processed), cycle=17, now=NOW)

    assert events == []
    metrics = diagnostics.snapshot()
    assert metrics.scanner_snapshots_published == 1
    assert metrics.scanner_snapshots_suppressed_unchanged == 29_999

    publisher.publish(_snapshot(_candidate(), processed=30_001), cycle=17, now=NOW)
    assert publisher.last_changed is True
    assert len(events) == 1
    assert events[0].event_type == "candidate_qualified"

    publisher.publish(_snapshot(_candidate(), processed=30_002), cycle=17, now=NOW)
    assert publisher.last_changed is False
    assert len(events) == 1


def test_technical_only_candidate_is_visible_without_becoming_ranked() -> None:
    events = []
    sequences = count(1)
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: next(sequences),
        source="display-test",
        stale_after=timedelta(seconds=30),
    )

    watching = ScannerDecision(
        symbol="LUCY",
        qualified=False,
        score=82,
        metrics=ScannerMetrics(
            percentage_change=Decimal("14"),
            relative_volume=Decimal("7"),
            dollar_volume=Decimal("4000000"),
            spread_percent=Decimal("0.3"),
        ),
        passed_rules=("price_range", "relative_volume"),
        failed_rules=("news_catalyst",),
        timestamp=NOW,
        price=Decimal("4.20"),
        current_volume=Decimal("950000"),
        catalyst=CatalystType.NONE,
        technical_qualifies_without_catalyst=True,
        scanner_rank=2,
    )
    rejected = ScannerDecision(
        symbol="NOPE",
        qualified=False,
        score=40,
        metrics=ScannerMetrics(
            percentage_change=Decimal("2"),
            relative_volume=Decimal("1"),
            dollar_volume=Decimal("200000"),
            spread_percent=Decimal("2"),
        ),
        passed_rules=(),
        failed_rules=("percentage_change", "relative_volume"),
        timestamp=NOW,
        price=Decimal("3"),
        technical_qualifies_without_catalyst=False,
        scanner_rank=9,
    )

    snapshot = ScannerSnapshot(
        timestamp=NOW,
        active_symbols=("LUCY", "NOPE"),
        decisions=(watching, rejected),
        ranked_candidates=(),
        processed_events=2,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )

    publisher.publish(snapshot, cycle=1, now=NOW)

    watch_events = [
        event
        for event in events
        if event.event_type == "SCANNER_CANDIDATE_WATCHING"
    ]

    assert len(watch_events) == 1
    assert watch_events[0].symbol == "LUCY"
    assert watch_events[0].watchlist is not None
    assert watch_events[0].watchlist.subscribed is True

    metadata = dict(watch_events[0].watchlist.metadata or ())
    assert metadata["scanner_rank"] == "2"
    assert metadata["scanner_score"] == "82"
    assert metadata["scanner_classification"] == "WATCHING"
    assert metadata["technical_qualifies_without_catalyst"] == "true"

    assert not any(
        event.symbol == "NOPE"
        and event.watchlist is not None
        and event.watchlist.subscribed is True
        for event in events
    )


def test_repeated_technical_only_snapshot_does_not_enter_strict_exit_lifecycle() -> None:
    events = []
    sequences = count(1)
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: next(sequences),
        source="display-test",
        stale_after=timedelta(seconds=30),
    )

    watching = ScannerDecision(
        symbol="DAIC",
        qualified=False,
        score=78,
        metrics=ScannerMetrics(
            percentage_change=Decimal("12"),
            relative_volume=Decimal("6"),
            dollar_volume=Decimal("3500000"),
            spread_percent=Decimal("0.4"),
        ),
        passed_rules=("price_range", "relative_volume"),
        failed_rules=("news_catalyst",),
        timestamp=NOW,
        price=Decimal("3.75"),
        current_volume=Decimal("800000"),
        catalyst=CatalystType.NONE,
        technical_qualifies_without_catalyst=True,
        scanner_rank=1,
    )

    first = ScannerSnapshot(
        timestamp=NOW,
        active_symbols=("DAIC",),
        decisions=(watching,),
        ranked_candidates=(),
        processed_events=1,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )
    second = ScannerSnapshot(
        timestamp=NOW + timedelta(seconds=1),
        active_symbols=("DAIC",),
        decisions=(watching,),
        ranked_candidates=(),
        processed_events=2,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )

    publisher.publish(first, cycle=1, now=NOW)
    publisher.publish(
        second,
        cycle=2,
        now=NOW + timedelta(seconds=1),
    )

    assert publisher._displayed_symbols == {"DAIC"}
    assert publisher._published_symbols == set()
    assert publisher._published_decisions == {}

    assert any(
        event.event_type == "SCANNER_CANDIDATE_WATCHING"
        and event.symbol == "DAIC"
        for event in events
    )


def test_single_soft_technical_failure_is_near_miss_but_safety_failure_is_hidden() -> None:
    events = []
    sequences = count(1)
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: next(sequences),
        source="near-miss-test",
        stale_after=timedelta(seconds=30),
    )

    near_miss = ScannerDecision(
        symbol="CLOSE",
        qualified=False,
        score=72,
        metrics=ScannerMetrics(
            percentage_change=Decimal("9.5"),
            relative_volume=Decimal("7"),
            dollar_volume=Decimal("6000000"),
            spread_percent=Decimal("0.3"),
        ),
        passed_rules=(
            "price_range",
            "relative_volume",
            "float_verified",
            "low_float",
            "news_catalyst",
            "tradable",
            "not_halted",
            "dollar_volume",
            "spread",
        ),
        failed_rules=("percentage_change",),
        timestamp=NOW,
        price=Decimal("4.50"),
        current_volume=Decimal("1400000"),
        catalyst=CatalystType.EARNINGS,
        technical_qualifies_without_catalyst=False,
        technical_passed_rules=(
            "price_range",
            "relative_volume",
            "float_verified",
            "low_float",
            "tradable",
            "not_halted",
            "dollar_volume",
            "spread",
        ),
        technical_failed_rules=("percentage_change",),
        scanner_rank=3,
    )

    unsafe = ScannerDecision(
        symbol="HALT",
        qualified=False,
        score=90,
        metrics=ScannerMetrics(
            percentage_change=Decimal("30"),
            relative_volume=Decimal("10"),
            dollar_volume=Decimal("9000000"),
            spread_percent=Decimal("0.2"),
        ),
        passed_rules=(
            "price_range",
            "percentage_change",
            "relative_volume",
            "float_verified",
            "low_float",
            "news_catalyst",
            "tradable",
            "dollar_volume",
            "spread",
        ),
        failed_rules=("not_halted",),
        timestamp=NOW,
        price=Decimal("6"),
        current_volume=Decimal("1500000"),
        catalyst=CatalystType.EARNINGS,
        technical_qualifies_without_catalyst=False,
        technical_passed_rules=(
            "price_range",
            "percentage_change",
            "relative_volume",
            "float_verified",
            "low_float",
            "tradable",
            "dollar_volume",
            "spread",
        ),
        technical_failed_rules=("not_halted",),
        scanner_rank=1,
    )

    snapshot = ScannerSnapshot(
        timestamp=NOW,
        active_symbols=("CLOSE", "HALT"),
        decisions=(near_miss, unsafe),
        ranked_candidates=(),
        processed_events=2,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )

    publisher.publish(snapshot, cycle=1, now=NOW)

    near_events = [
        event
        for event in events
        if event.event_type == "SCANNER_CANDIDATE_NEAR_MISS"
    ]

    assert len(near_events) == 1
    assert near_events[0].symbol == "CLOSE"
    assert near_events[0].watchlist is not None

    metadata = dict(near_events[0].watchlist.metadata or ())
    assert metadata["scanner_classification"] == "NEAR MISS"
    assert metadata["scanner_failed_rules"] == "percentage_change"

    assert not any(
        event.symbol == "HALT"
        and event.watchlist is not None
        and event.watchlist.subscribed is True
        for event in events
    )


def test_candidate_exit_logs_failed_rule_and_transition_values(caplog) -> None:
    publisher = ScannerSnapshotPublisher(
        lambda event: None,
        lambda: 1,
        source="diagnostic-test",
        stale_after=timedelta(seconds=30),
    )
    qualified = _candidate("WYHG")
    rejected = ScannerDecision(
        symbol="WYHG",
        qualified=False,
        score=71,
        metrics=ScannerMetrics(
            percentage_change=Decimal("12"),
            relative_volume=Decimal("6"),
            dollar_volume=Decimal("5000000"),
            spread_percent=Decimal("1.4"),
        ),
        passed_rules=("price_range",),
        failed_rules=("spread",),
        timestamp=NOW,
        price=Decimal("5"),
        diagnostic_rule_values=(("spread", "1.4"),),
    )

    with caplog.at_level("INFO", logger="atlas.scanner"):
        publisher.publish(_snapshot(qualified), cycle=1, now=NOW)
        publisher.publish(ScannerSnapshot(
            timestamp=NOW,
            active_symbols=("WYHG",),
            decisions=(rejected,),
            ranked_candidates=(),
            processed_events=2,
            ignored_events=0,
            reference_failures=(),
            session="REGULAR",
        ), cycle=2, now=NOW)

    assert "event_type=candidate_entered symbol=WYHG" in caplog.text
    assert "event_type=candidate_exited symbol=WYHG" in caplog.text
    assert "failed_rule_on_exit=spread" in caplog.text
    assert "previous_value=0.2 current_value=1.4" in caplog.text


def test_event_store_excludes_raw_market_tape_but_keeps_lifecycle() -> None:
    projection = TimelineProjection(OperationsBus())
    raw_types = (
        "MARKET_DATA_QUOTE_RECEIVED",
        "MARKET_DATA_TRADE_RECEIVED",
        "MARKET_DATA_SNAPSHOT_RECEIVED",
    )
    for sequence in range(1, 30_001):
        projection(
            PaperRuntimeEvent(
                sequence=sequence,
                timestamp=NOW,
                event_type=raw_types[sequence % len(raw_types)],
                message="Sanitized market-data observation.",
                cycle=17,
                source="load-test",
            )
        )

    assert projection.snapshot == TimelineReadModelSnapshot.initial()

    projection(
        PaperRuntimeEvent(
            sequence=30_001,
            timestamp=NOW,
            event_type="MARKET_DATA_DISCONNECTED",
            message="Market data disconnected.",
            cycle=17,
            source="load-test",
        )
    )
    assert len(projection.snapshot.entries) == 1


def test_runtime_scanner_and_rest_driver_work_runs_off_calling_gui_thread() -> None:
    bus = OperationsBus()
    gui_thread = get_ident()
    ran_on = []
    stopped = Event()

    class Driver:
        environment = "PAPER"
        active_model = "read-only-load-test"
        cycles_completed = 0

        def run(self, *, stop_event, cycle_sink) -> None:
            ran_on.append(get_ident())
            self.cycles_completed = 1
            cycle_sink(1)
            stopped.set()

    service = RuntimeService(bus, Driver)
    try:
        assert service.start() is True
        assert stopped.wait(2)
        assert service.wait(2)
        assert ran_on and ran_on[0] != gui_thread
    finally:
        service.close()


def test_shutdown_is_clean_while_high_frequency_state_updates_arrive() -> None:
    bus = OperationsBus()
    finished = Event()

    class Driver:
        environment = "PAPER"
        active_model = "read-only-load-test"
        cycles_completed = 0

        def run(self, *, stop_event, cycle_sink) -> None:
            while not stop_event.is_set():
                self.cycles_completed += 1
                cycle_sink(self.cycles_completed)
            finished.set()

    service = RuntimeService(bus, Driver)
    assert service.start() is True
    assert service.stop() is True
    assert service.wait(2)
    assert finished.is_set()


def test_27_symbol_30k_mixed_event_load_is_full_fidelity_and_bounded() -> None:
    symbols = tuple(f"S{index:02d}" for index in range(27))
    events = deque()
    for index in range(30_000):
        symbol = symbols[index % len(symbols)]
        timestamp = NOW + timedelta(milliseconds=index)
        event_kind = index % 3
        if event_kind == 0:
            event_type = MarketEventType.QUOTE
            payload = QuotePayload(
                Decimal("4.99"), Decimal("5.01"), Decimal("10"), Decimal("10")
            )
        elif event_kind == 1:
            event_type = MarketEventType.TRADE
            payload = TradePayload(Decimal("5"), Decimal("1"), f"t-{index}")
        else:
            event_type = MarketEventType.BOOK_SNAPSHOT
            payload = OrderBookSnapshotPayload(
                (BookLevel(Decimal("4.99"), Decimal("10")),),
                (BookLevel(Decimal("5.01"), Decimal("10")),),
            )
            if index % 30 == 2:
                timestamp -= timedelta(seconds=1)
        events.append(
            MarketEvent(index + 1, timestamp, symbol, "load-test", event_type, payload)
        )

    class Transport:
        connected = False

        def connect(self) -> None:
            self.connected = True

        def disconnect(self) -> None:
            self.connected = False

        def subscribe(self, channels) -> None:
            self.channels = tuple(channels)

        def read_event(self):
            return events.popleft() if events else None

    class Engine:
        def __init__(self) -> None:
            self.consumed = 0
            self.stale = 0
            self.latest = {}

        def refresh_universe(self, asset_classes, *, force_reference_refresh=False):
            assert asset_classes == (AssetClass.STOCK,)
            return symbols

        @property
        def subscription_symbols(self):
            return symbols

        def consume(self, event):
            self.consumed += 1
            previous = self.latest.get(event.symbol)
            if previous is not None and event.timestamp < previous:
                self.stale += 1
            else:
                self.latest[event.symbol] = event.timestamp
            return None

        def snapshot(self, *, limit=25):
            candidates = (_candidate(),) if self.consumed >= 20_000 else ()
            return _snapshot(*candidates, processed=self.consumed)

    transport = Transport()
    engine = Engine()
    scanner = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("QUOTE", "TRADE", "SNAPSHOT"),
        maximum_events_per_cycle=1000,
    )
    diagnostics = PerformanceDiagnostics()
    publications = []
    sequence = count(1)
    publisher = ScannerSnapshotPublisher(
        publications.append,
        lambda: next(sequence),
        source="load-test",
        stale_after=timedelta(seconds=30),
        diagnostics=diagnostics,
    )

    scanner.start(asset_classes=(AssetClass.STOCK,))
    evaluations = 0
    while events:
        cycle = scanner.run_available()
        evaluations += 1
        publisher.publish(scanner.snapshot(), cycle=17, now=NOW)
        assert cycle.events_read <= 1000
    scanner.stop()
    scanner.disconnect()

    assert engine.consumed == 30_000
    assert engine.stale > 0
    assert len(events) == 0
    assert evaluations == 30
    assert len(publications) == 1
    assert publications[0].event_type == "candidate_qualified"
    metrics = diagnostics.snapshot()
    assert metrics.scanner_snapshots_published == 2
    assert metrics.scanner_snapshots_suppressed_unchanged == 28
