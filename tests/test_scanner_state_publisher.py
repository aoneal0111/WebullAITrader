from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.composition.scanner_state_publisher import ScannerStatePublisher
from app.momentum_scanner.models import ScannerDecision, ScannerMetrics
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    ScannerSnapshotUpdated,
)
from app.realtime_scanner.models import ScannerSnapshot


def make_decision(
    symbol: str,
    *,
    score: int,
) -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol,
        qualified=True,
        score=score,
        metrics=ScannerMetrics(
            percentage_change=Decimal("25"),
            relative_volume=Decimal("6"),
            dollar_volume=Decimal("10000000"),
            spread_percent=Decimal("0.25"),
        ),
        passed_rules=("price_range",),
        failed_rules=(),
    )


def make_snapshot(
    *decisions: ScannerDecision,
    timestamp: datetime | None = None,
) -> ScannerSnapshot:
    ranked_candidates = tuple(decisions)

    return ScannerSnapshot(
        timestamp=timestamp or datetime.now(timezone.utc),
        active_symbols=tuple(
            decision.symbol
            for decision in ranked_candidates
        ),
        decisions=ranked_candidates,
        ranked_candidates=ranked_candidates,
        processed_events=len(ranked_candidates),
        ignored_events=0,
        reference_failures=(),
    )


def test_publisher_emits_ranked_scanner_snapshot_event() -> None:
    bus = OperationsBus()
    received: list[ScannerSnapshotUpdated] = []

    bus.subscribe(
        ScannerSnapshotUpdated,
        received.append,
    )

    timestamp = datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc)
    decisions = (
        make_decision("NVDA", score=95),
        make_decision("AMD", score=90),
        make_decision("META", score=85),
    )
    publisher = ScannerStatePublisher(bus)

    publisher(
        make_snapshot(
            *decisions,
            timestamp=timestamp,
        )
    )

    assert len(received) == 1

    event = received[0]
    assert event.source == "scanner-runtime"
    assert event.occurred_at == timestamp
    assert event.candidates == ("NVDA", "AMD", "META")
    assert event.ranked_candidates == decisions


def test_publisher_updates_application_scanner_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    publisher = ScannerStatePublisher(bus)

    timestamp = datetime(2026, 7, 25, 15, 31, tzinfo=timezone.utc)
    decisions = (
        make_decision("AAPL", score=92),
        make_decision("MSFT", score=88),
    )

    publisher(
        make_snapshot(
            *decisions,
            timestamp=timestamp,
        )
    )

    scanner = store.snapshot().scanner

    assert scanner.candidates == ("AAPL", "MSFT")
    assert scanner.ranked_candidates == decisions
    assert scanner.last_scan_at == timestamp
    assert scanner.status == "Active"

    store.close()


def test_publisher_rejects_invalid_snapshot() -> None:
    bus = OperationsBus()
    publisher = ScannerStatePublisher(bus)

    with pytest.raises(TypeError, match="ScannerSnapshot"):
        publisher(object())  # type: ignore[arg-type]


def test_publisher_rejects_empty_source() -> None:
    bus = OperationsBus()

    with pytest.raises(ValueError, match="source must not be empty"):
        ScannerStatePublisher(bus, source="   ")
