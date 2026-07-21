from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.operations import LiveScannerSnapshotSource, ScannerRuntimeCycle

NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str


@dataclass(frozen=True, slots=True)
class ScannerView:
    ranked_candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class StreamCycle:
    events_read: int
    decisions_created: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str


class StubScannerCoordinator:
    def __init__(self, candidates: tuple[Candidate, ...] = (Candidate("AAPL"),)) -> None:
        self.candidates = candidates
        self.maximum_events: list[int | None] = []
        self.limits: list[int] = []

    def run_available(self, *, maximum_events: int | None = None) -> StreamCycle:
        self.maximum_events.append(maximum_events)
        return StreamCycle(events_read=3, decisions_created=2)

    def snapshot(self, *, limit: int = 25) -> ScannerView:
        self.limits.append(limit)
        return ScannerView(self.candidates)


def test_source_drains_bounded_events_and_preserves_rank_order() -> None:
    coordinator = StubScannerCoordinator((Candidate("MSFT"), Candidate("aapl")))
    cycles: list[ScannerRuntimeCycle] = []
    source = LiveScannerSnapshotSource(
        coordinator,
        lambda symbol, timestamp: Snapshot(symbol),
        candidate_limit=2,
        maximum_events_per_cycle=50,
        cycle_sink=cycles.append,
    )

    snapshots = tuple(source(NOW))

    assert tuple(item.symbol for item in snapshots) == ("MSFT", "AAPL")
    assert coordinator.maximum_events == [50]
    assert coordinator.limits == [2]
    assert cycles == [source.last_cycle]
    assert cycles[0].events_read == 3
    assert cycles[0].decisions_created == 2
    assert cycles[0].ranked_symbols == ("MSFT", "AAPL")
    assert cycles[0].resolved_symbols == ("MSFT", "AAPL")
    assert cycles[0].missing_symbols == ()


def test_source_records_missing_market_snapshots_without_fabricating_data() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator((Candidate("AAPL"), Candidate("MSFT"))),
        lambda symbol, timestamp: None if symbol == "MSFT" else Snapshot(symbol),
    )

    assert tuple(item.symbol for item in source(NOW)) == ("AAPL",)
    assert source.last_cycle is not None
    assert source.last_cycle.missing_symbols == ("MSFT",)


def test_empty_ranked_candidates_return_empty_batch() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator(()),
        lambda symbol, timestamp: Snapshot(symbol),
    )

    assert tuple(source(NOW)) == ()
    assert source.last_cycle is not None
    assert source.last_cycle.ranked_symbols == ()


def test_source_rejects_duplicate_symbols_after_normalization() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator((Candidate("AAPL"), Candidate(" aapl "))),
        lambda symbol, timestamp: Snapshot(symbol),
    )

    with pytest.raises(ValueError, match="duplicate"):
        tuple(source(NOW))


def test_source_rejects_blank_candidate_symbol() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator((Candidate("  "),)),
        lambda symbol, timestamp: Snapshot(symbol),
    )

    with pytest.raises(ValueError, match="symbol is required"):
        tuple(source(NOW))


def test_source_rejects_resolved_symbol_mismatch() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator(),
        lambda symbol, timestamp: Snapshot("MSFT"),
    )

    with pytest.raises(ValueError, match="does not match"):
        tuple(source(NOW))


def test_source_requires_timezone_aware_cycle_timestamp() -> None:
    source = LiveScannerSnapshotSource(
        StubScannerCoordinator(),
        lambda symbol, timestamp: Snapshot(symbol),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        tuple(source(datetime(2026, 7, 20, 14, 0)))


def test_constructor_rejects_nonpositive_limits() -> None:
    coordinator = StubScannerCoordinator()

    with pytest.raises(ValueError, match="candidate limit"):
        LiveScannerSnapshotSource(coordinator, lambda symbol, timestamp: None, candidate_limit=0)
    with pytest.raises(ValueError, match="maximum events"):
        LiveScannerSnapshotSource(
            coordinator,
            lambda symbol, timestamp: None,
            maximum_events_per_cycle=0,
        )
