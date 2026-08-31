from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from time import perf_counter

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.momentum_scanner import CatalystType, ScannerDecision, ScannerMetrics
from app.operations.runtime import PaperRuntimeEvent, RuntimeHealthUpdate
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.operations_core import OperationsBus
from app.paper_trading.models import PaperFill
from app.read_models.health_projection import HealthProjection
from app.read_models.watchlist_projection import WatchlistProjection
from app.realtime_scanner import ScannerSnapshot


NOW = datetime(2026, 8, 31, 14, 30, tzinfo=UTC)


def _mark(sequence: int, symbol: str = "ZZZZ") -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=NOW + timedelta(microseconds=sequence),
        event_type="MARK_UPDATED",
        message="Raw market mark.",
        cycle=sequence // 100,
        symbol=symbol,
        mark_price=Decimal("5.00"),
        source="bounded-projection-load",
        health=RuntimeHealthUpdate(
            runtime_status="RUNNING",
            market_data_status="CONNECTED",
            streaming_status="CONNECTED",
            subscription_status="ACCEPTED",
        ),
    )


def _candidate(symbol: str, rank: int, price: str = "5.00") -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol,
        qualified=True,
        score=100 - rank,
        metrics=ScannerMetrics(
            percentage_change=Decimal("12"),
            relative_volume=Decimal("6"),
            dollar_volume=Decimal("5000000"),
            spread_percent=Decimal("0.2"),
        ),
        passed_rules=("price_range",),
        failed_rules=(),
        timestamp=NOW,
        price=Decimal(price),
        current_volume=Decimal("1000000"),
        catalyst=CatalystType.OTHER,
        scanner_rank=rank,
    )


def _snapshot(*candidates: ScannerDecision) -> ScannerSnapshot:
    return ScannerSnapshot(
        timestamp=NOW,
        active_symbols=tuple(item.symbol for item in candidates),
        decisions=tuple(candidates),
        ranked_candidates=tuple(candidates),
        processed_events=0,
        ignored_events=0,
        reference_failures=(),
        session="REGULAR",
    )


def test_projection_identity_state_remains_bounded_after_100k_events() -> None:
    health = HealthProjection(OperationsBus())
    watchlist = WatchlistProjection(OperationsBus())

    for sequence in range(1, 100_001):
        event = _mark(sequence)
        health(event)
        watchlist(event)

    assert not hasattr(health, "_seen_events")
    assert not hasattr(watchlist, "_seen_events")
    assert health._latest_sequence == {"bounded-projection-load": 100_000}
    assert watchlist._latest_sequence == {"bounded-projection-load": 100_000}
    assert watchlist.snapshot.entries == ()


def test_zero_positions_ignore_100k_raw_marks_and_relevant_history_is_bounded() -> None:
    pipeline = create_runtime_projection_pipeline(
        operations_bus=OperationsBus(),
        account_id="paper",
    )
    intelligence = pipeline.portfolio_intelligence_projection

    for sequence in range(1, 100_001):
        pipeline.sink(_mark(sequence))

    assert dict(intelligence._history) == {}

    fill = PaperFill(
        "open-1", "ABCD", "BUY", Decimal("1"), Decimal("5"),
        Decimal("5"), Decimal("0"), NOW + timedelta(seconds=1),
    )
    pipeline.sink(PaperRuntimeEvent(
        sequence=100_001,
        timestamp=fill.timestamp,
        event_type="FILL",
        message="Position opened.",
        cycle=1,
        symbol="ABCD",
        fill=fill,
        mark_price=Decimal("5"),
        source="bounded-projection-load",
    ))
    for sequence in range(100_002, 100_202):
        pipeline.sink(_mark(sequence, "ABCD"))

    assert len(intelligence._history["ABCD"]) == 61
    assert intelligence.snapshot is not None
    assert intelligence.snapshot.positions[0].market_value == Decimal("5")

    close = PaperFill(
        "close-1", "ABCD", "SELL", Decimal("1"), Decimal("5"),
        Decimal("5"), Decimal("0"), NOW + timedelta(seconds=2),
    )
    pipeline.sink(PaperRuntimeEvent(
        sequence=100_202,
        timestamp=close.timestamp,
        event_type="FILL",
        message="Position closed.",
        cycle=2,
        symbol="ABCD",
        fill=close,
        mark_price=Decimal("5"),
        source="bounded-projection-load",
    ))
    for sequence in range(100_203, 100_303):
        pipeline.sink(_mark(sequence, "ABCD"))

    assert "ABCD" not in intelligence._history
    assert intelligence.snapshot is not None
    assert intelligence.snapshot.positions == ()


def test_scanner_publishes_only_candidate_deltas_and_retains_full_snapshot() -> None:
    events: list[PaperRuntimeEvent] = []
    publisher = ScannerSnapshotPublisher(
        events.append,
        count(1).__next__,
        source="delta-test",
        stale_after=timedelta(seconds=30),
    )
    first = _snapshot(_candidate("AAAA", 1), _candidate("BBBB", 2))
    publisher.publish(first, cycle=1, now=NOW)
    assert [event.symbol for event in events] == ["AAAA", "BBBB"]

    events.clear()
    changed = _snapshot(_candidate("AAAA", 1, "5.25"), _candidate("BBBB", 2))
    publisher.publish(changed, cycle=2, now=NOW)

    assert [event.symbol for event in events] == ["AAAA"]
    assert publisher.authoritative_snapshot is changed


def test_full_watchlist_state_reconstructs_from_initial_snapshot_and_deltas() -> None:
    events: list[PaperRuntimeEvent] = []
    publisher = ScannerSnapshotPublisher(
        events.append,
        count(1).__next__,
        source="recovery-test",
        stale_after=timedelta(seconds=30),
    )
    publisher.publish(
        _snapshot(_candidate("AAAA", 1), _candidate("BBBB", 2)),
        cycle=1,
        now=NOW,
    )
    publisher.publish(
        _snapshot(_candidate("BBBB", 1), _candidate("AAAA", 2, "5.25")),
        cycle=2,
        now=NOW,
    )
    publisher.publish(_snapshot(_candidate("AAAA", 1, "5.25")), cycle=3, now=NOW)
    publisher.publish(
        _snapshot(_candidate("AAAA", 1, "5.25"), _candidate("BBBB", 2, "5.50")),
        cycle=4,
        now=NOW,
    )

    projection = WatchlistProjection(OperationsBus())
    for event in events:
        projection(event)

    by_symbol = {entry.symbol: entry for entry in projection.snapshot.entries}
    assert projection.snapshot.ordered_symbols == ("AAAA", "BBBB")
    assert by_symbol["AAAA"].latest_price == "5.25"
    assert by_symbol["BBBB"].latest_price == "5.50"
    assert dict(by_symbol["AAAA"].metadata)["scanner_rank"] == "1"
    assert dict(by_symbol["BBBB"].metadata)["scanner_rank"] == "2"


def test_late_session_projection_throughput_does_not_collapse() -> None:
    pipeline = create_runtime_projection_pipeline(
        operations_bus=OperationsBus(),
        account_id="paper",
    )
    windows: list[float] = []
    sequence = 1
    for _ in range(20):
        started = perf_counter()
        for _ in range(5_000):
            pipeline.sink(_mark(sequence))
            sequence += 1
        windows.append(5_000 / (perf_counter() - started))

    early = sum(windows[:2]) / 2
    late = sum(windows[-2:]) / 2
    assert late >= early * 0.75
    assert late >= 195
