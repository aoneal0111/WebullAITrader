from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from app.operations.scanner_runtime import LiveScannerSnapshotSource


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    ranked_candidates: tuple[Candidate, ...]


class Coordinator:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot_value = snapshot

    def run_available(self, *, maximum_events: int | None = None):
        assert maximum_events == 1000
        return SimpleNamespace(
            events_read=2,
            decisions_created=2,
        )

    def snapshot(self, *, limit: int = 25) -> Snapshot:
        assert limit == 25
        return self.snapshot_value


def test_live_snapshot_source_publishes_exact_scanner_snapshot() -> None:
    snapshot = Snapshot(
        ranked_candidates=(
            Candidate("MSFT"),
            Candidate("AAPL"),
        )
    )
    published: list[Snapshot] = []
    cycles = []

    source = LiveScannerSnapshotSource(
        Coordinator(snapshot),
        lambda symbol, timestamp: SimpleNamespace(symbol=symbol),
        cycle_sink=cycles.append,
        snapshot_sink=published.append,
    )

    resolved = source(datetime(2026, 7, 25, 20, 0, tzinfo=UTC))

    assert tuple(item.symbol for item in resolved) == ("MSFT", "AAPL")
    assert published == [snapshot]

    assert len(cycles) == 1
    assert cycles[0].ranked_symbols == ("MSFT", "AAPL")
    assert cycles[0].resolved_symbols == ("MSFT", "AAPL")
    assert cycles[0].missing_symbols == ()
