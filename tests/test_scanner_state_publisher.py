from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.composition.scanner_state_publisher import ScannerStatePublisher
from app.operations.scanner_runtime import ScannerRuntimeCycle
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    ScannerSnapshotUpdated,
)


def make_cycle(
    *symbols: str,
    timestamp: datetime | None = None,
) -> ScannerRuntimeCycle:
    return ScannerRuntimeCycle(
        timestamp=timestamp or datetime.now(timezone.utc),
        events_read=12,
        decisions_created=len(symbols),
        ranked_symbols=tuple(symbols),
        resolved_symbols=tuple(symbols),
        missing_symbols=(),
    )


def test_publisher_emits_scanner_snapshot_event() -> None:
    bus = OperationsBus()
    received: list[ScannerSnapshotUpdated] = []

    bus.subscribe(
        ScannerSnapshotUpdated,
        received.append,
    )

    timestamp = datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc)
    publisher = ScannerStatePublisher(bus)

    publisher(
        make_cycle(
            "NVDA",
            "AMD",
            "META",
            timestamp=timestamp,
        )
    )

    assert len(received) == 1

    event = received[0]
    assert event.source == "scanner-runtime"
    assert event.occurred_at == timestamp
    assert event.candidates == ("NVDA", "AMD", "META")


def test_publisher_updates_application_scanner_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    publisher = ScannerStatePublisher(bus)

    timestamp = datetime(2026, 7, 25, 15, 31, tzinfo=timezone.utc)

    publisher(
        make_cycle(
            "AAPL",
            "MSFT",
            timestamp=timestamp,
        )
    )

    scanner = store.snapshot().scanner

    assert scanner.candidates == ("AAPL", "MSFT")
    assert scanner.last_scan_at == timestamp
    assert scanner.status == "Active"

    store.close()


def test_publisher_rejects_invalid_cycle() -> None:
    bus = OperationsBus()
    publisher = ScannerStatePublisher(bus)

    with pytest.raises(TypeError, match="ScannerRuntimeCycle"):
        publisher(object())  # type: ignore[arg-type]


def test_publisher_rejects_empty_source() -> None:
    bus = OperationsBus()

    with pytest.raises(ValueError, match="source must not be empty"):
        ScannerStatePublisher(bus, source="   ")
