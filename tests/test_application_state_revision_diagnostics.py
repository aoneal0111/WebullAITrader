from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from app.momentum_scanner import CatalystType, ScannerDecision, ScannerMetrics
from app.memory_observability.runtime import MemoryObservability
from app.operations.runtime import PaperRuntimeEvent
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsEvent,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopping,
    TimelineUpdated,
)
from app.performance_diagnostics import PerformanceDiagnostics
from app.read_models.timeline_projection import TimelineProjection
from app.realtime_scanner import ScannerSnapshot


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class UnknownOperationsEvent(OperationsEvent):
    pass


@dataclass(frozen=True, slots=True)
class CascadeA(OperationsEvent):
    pass


@dataclass(frozen=True, slots=True)
class CascadeB(OperationsEvent):
    pass


@dataclass(frozen=True, slots=True)
class CascadeC(OperationsEvent):
    pass


def _runtime_event(sequence: int, event_type: str) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=NOW + timedelta(minutes=sequence),
        event_type=event_type,
        message=event_type,
        cycle=sequence,
        source="diagnostic-test",
    )


def _candidate(symbol: str = "TEST") -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol,
        qualified=True,
        score=90,
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


def _scanner_snapshot(candidate: ScannerDecision | None = None) -> ScannerSnapshot:
    candidates = () if candidate is None else (candidate,)
    return ScannerSnapshot(
        timestamp=NOW,
        active_symbols=tuple(item.symbol for item in candidates),
        decisions=candidates,
        ranked_candidates=candidates,
        processed_events=1,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )


def test_state_store_counts_received_revisions_and_no_revision_events() -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)
    store = ApplicationStateStore(bus, diagnostics=diagnostics)

    bus.publish(RuntimeStarting())
    event = TimelineUpdated(entries=())
    bus.publish(event)
    metrics = store.memory_metrics()

    assert metrics["events_received_total"] == 2
    assert metrics["revisions_created_total"] == 1
    assert metrics["events_without_revision"] == 1
    assert metrics["events_received_RuntimeStarting"] == 1
    assert metrics["events_received_TimelineUpdated"] == 1
    assert metrics["revisions_created_RuntimeStarting"] == 1

    store.close()


def test_unknown_event_type_uses_one_bounded_bucket() -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)
    store = ApplicationStateStore(bus, diagnostics=diagnostics)

    bus.publish(UnknownOperationsEvent())
    metrics = store.memory_metrics()

    assert metrics["events_received_UNKNOWN"] == 1
    assert metrics["revisions_created_UNKNOWN"] == 1
    assert not any("UnknownOperationsEvent" in key for key in metrics)
    store.close()


def test_synchronous_derived_publications_report_root_depth_and_root_type() -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)

    def derive(event: RuntimeStarted) -> None:
        bus.publish(RuntimeStopping())

    bus.subscribe(RuntimeStarted, derive)
    bus.publish(RuntimeStarted(active_model="diagnostic"))
    metrics = bus.memory_metrics()

    assert metrics["root_publications"] == 1
    assert metrics["derived_publications"] == 1
    assert metrics["max_cascade_depth"] == 2
    assert metrics["publications_depth_1"] == 1
    assert metrics["publications_depth_2"] == 1
    assert metrics["derived_from_RuntimeStarted"] == 1
    assert metrics["derived_from_UNKNOWN"] == 0


def test_cascade_depth_and_root_attribution_cover_none_one_multi_and_siblings() -> None:
    def metrics_for(install) -> dict[str, int]:
        diagnostics = PerformanceDiagnostics()
        bus = OperationsBus(diagnostics=diagnostics)
        install(bus)
        bus.publish(CascadeA())
        return bus.memory_metrics()

    none = metrics_for(lambda bus: None)
    assert none["root_publications"] == 1
    assert none["derived_publications"] == 0
    assert none["max_cascade_depth"] == 1
    assert none["publications_depth_1"] == 1

    one = metrics_for(
        lambda bus: bus.subscribe(CascadeA, lambda event: bus.publish(CascadeB()))
    )
    assert one["derived_publications"] == 1
    assert one["max_cascade_depth"] == 2
    assert one["publications_depth_2"] == 1
    assert one["derived_from_UNKNOWN"] == 1

    def install_multi(bus: OperationsBus) -> None:
        bus.subscribe(CascadeA, lambda event: bus.publish(CascadeB()))
        bus.subscribe(CascadeB, lambda event: bus.publish(CascadeC()))

    multi = metrics_for(install_multi)
    assert multi["derived_publications"] == 2
    assert multi["max_cascade_depth"] == 3
    assert multi["publications_depth_3"] == 1
    assert multi["derived_from_UNKNOWN"] == 2

    def install_siblings(bus: OperationsBus) -> None:
        def publish_siblings(event: CascadeA) -> None:
            bus.publish(CascadeB())
            bus.publish(CascadeC())

        bus.subscribe(CascadeA, publish_siblings)

    siblings = metrics_for(install_siblings)
    assert siblings["derived_publications"] == 2
    assert siblings["max_cascade_depth"] == 2
    assert siblings["publications_depth_2"] == 2
    assert siblings["derived_from_UNKNOWN"] == 2


def test_cascade_and_scanner_contexts_restore_after_exceptions() -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)

    def fail(event: CascadeA) -> None:
        raise RuntimeError("diagnostic test")

    bus.subscribe(CascadeA, fail)
    try:
        bus.publish(CascadeA())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected listener failure")

    assert diagnostics.scanner_context_active() is False
    with diagnostics.scanner_event_context():
        assert diagnostics.scanner_context_active() is True
        try:
            raise RuntimeError("scanner diagnostic test")
        except RuntimeError:
            pass
    assert diagnostics.scanner_context_active() is False

    bus.publish(CascadeB())
    metrics = bus.memory_metrics()
    assert metrics["root_publications"] == 2
    assert metrics["derived_publications"] == 0
    assert metrics["max_cascade_depth"] == 1


def test_cascade_contexts_are_isolated_between_threads() -> None:
    from threading import Barrier, Thread

    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)
    barrier = Barrier(2)

    def derive(event: CascadeA) -> None:
        barrier.wait()
        bus.publish(CascadeB())

    bus.subscribe(CascadeA, derive)
    threads = [Thread(target=lambda: bus.publish(CascadeA())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    metrics = bus.memory_metrics()
    assert metrics["root_publications"] == 2
    assert metrics["derived_publications"] == 2
    assert metrics["max_cascade_depth"] == 2
    assert metrics["derived_from_UNKNOWN"] == 2


def test_scanner_publication_counters_match_published_and_suppressed_events() -> None:
    diagnostics = PerformanceDiagnostics()
    emitted: list[PaperRuntimeEvent] = []
    publisher = ScannerSnapshotPublisher(
        emitted.append,
        count(1).__next__,
        source="scanner-diagnostic-test",
        stale_after=timedelta(seconds=30),
        diagnostics=diagnostics,
    )

    publisher.publish(_scanner_snapshot(), cycle=1, now=NOW)
    publisher.publish(_scanner_snapshot(), cycle=1, now=NOW)
    publisher.publish(_scanner_snapshot(_candidate()), cycle=1, now=NOW)

    metrics = publisher.memory_metrics()
    assert metrics["snapshots_published"] == 2
    assert metrics["snapshots_suppressed"] == 1
    assert metrics["related_events_emitted"] == len(emitted)
    assert metrics["state_revisions_per_published_snapshot_x1000"] == 0

    empty_publisher = ScannerSnapshotPublisher(
        emitted.append,
        count(1).__next__,
        source="scanner-empty-diagnostic-test",
        stale_after=timedelta(seconds=30),
        diagnostics=PerformanceDiagnostics(),
    )
    assert empty_publisher.memory_metrics()[
        "state_revisions_per_published_snapshot_x1000"
    ] == 0


def test_timeline_projection_copy_work_counts_below_at_and_above_cap() -> None:
    projection = TimelineProjection(OperationsBus(), maximum_entries=3)

    for sequence in range(1, 5):
        projection(_runtime_event(sequence, "STARTED"))

    metrics = projection.memory_metrics()
    assert metrics["updates"] == 4
    assert metrics["records_sorted"] == 10
    assert metrics["entries_rebuilt"] == 9
    assert metrics["seen_set_entries_rebuilt"] == 9
    assert metrics["bus_entries_reconstructed"] == 9


def test_application_timeline_copy_work_counts_at_cap_and_does_not_retain_events() -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)
    store = ApplicationStateStore(bus, timeline_limit=3, diagnostics=diagnostics)

    for _ in range(4):
        bus.publish(RuntimeStarting())

    metrics = store.memory_metrics()
    assert metrics["timeline_updates"] == 4
    assert metrics["timeline_entries_copied"] == 9
    assert all(isinstance(value, int) for value in metrics.values())
    store.close()


def test_periodic_memory_snapshot_exposes_counters_without_resetting_them(tmp_path) -> None:
    diagnostics = PerformanceDiagnostics()
    bus = OperationsBus(diagnostics=diagnostics)
    store = ApplicationStateStore(bus, diagnostics=diagnostics)
    bus.publish(RuntimeStarting())
    observer = MemoryObservability(
        {"application_state": store.memory_metrics},
        enabled=True,
        path=tmp_path / "memory.jsonl",
    )

    first = observer.sample()
    second = observer.sample()

    assert first is not None and second is not None
    first_metrics = dict(first.metrics)
    second_metrics = dict(second.metrics)
    assert first_metrics["application_state_events_received_total"] == 1
    assert first_metrics["application_state_revisions_created_total"] == 1
    assert second_metrics == first_metrics
    observer.close()
    store.close()
