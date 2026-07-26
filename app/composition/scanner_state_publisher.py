"""Publish live scanner cycles into immutable application state events."""

from __future__ import annotations

from app.operations.scanner_runtime import ScannerRuntimeCycle
from app.operations_core import OperationsBus, ScannerSnapshotUpdated


class ScannerStatePublisher:
    """Adapt ScannerRuntimeCycle callbacks to OperationsBus events."""

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

    def __call__(self, cycle: ScannerRuntimeCycle) -> None:
        if not isinstance(cycle, ScannerRuntimeCycle):
            raise TypeError("cycle must be ScannerRuntimeCycle")

        self._bus.publish(
            ScannerSnapshotUpdated(
                source=self._source,
                occurred_at=cycle.timestamp,
                candidates=cycle.ranked_symbols,
                ranked_candidates=(),
            )
        )


__all__ = ["ScannerStatePublisher"]
