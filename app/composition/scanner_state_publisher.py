"""Publish realtime scanner snapshots into immutable application state events."""

from __future__ import annotations

from app.operations_core import OperationsBus, ScannerSnapshotUpdated
from app.realtime_scanner.models import ScannerSnapshot


class ScannerStatePublisher:
    """Adapt immutable scanner snapshots to OperationsBus events."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        source: str = "scanner-runtime",
    ) -> None:
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("source must not be empty")

        self._bus = bus
        self._source = normalized_source

    def __call__(self, snapshot: ScannerSnapshot) -> None:
        if not isinstance(snapshot, ScannerSnapshot):
            raise TypeError("snapshot must be ScannerSnapshot")

        ranked_candidates = tuple(snapshot.ranked_candidates)

        self._bus.publish(
            ScannerSnapshotUpdated(
                source=self._source,
                occurred_at=snapshot.timestamp,
                candidates=tuple(
                    candidate.symbol
                    for candidate in ranked_candidates
                ),
                ranked_candidates=ranked_candidates,
            )
        )


__all__ = ["ScannerStatePublisher"]
