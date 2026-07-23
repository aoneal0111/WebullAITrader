from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.momentum_scanner.models import (
    AssetClass,
    ScannerDecision,
)
from app.reference_data.models import ReferenceRecord
from app.universe.models import UniverseSelection


@runtime_checkable
class UniverseSelector(Protocol):
    def select_all(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
    ) -> UniverseSelection:
        """Return the currently eligible stock and crypto universe."""


@runtime_checkable
class ReferenceLoader(Protocol):
    def get(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.STOCK,
        *,
        force_refresh: bool = False,
    ) -> ReferenceRecord:
        """Return reference data for a symbol."""


@runtime_checkable
class EventPipeline(Protocol):
    def consume(self, event: Any) -> ScannerDecision | None:
        """Consume one market event and return an updated decision."""


@runtime_checkable
class ReferenceSink(Protocol):
    def __call__(self, record: ReferenceRecord) -> None:
        """Store reference data where the scanner adapter can use it."""
