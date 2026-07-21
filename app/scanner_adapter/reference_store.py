from __future__ import annotations

from collections.abc import Iterable

from app.scanner_adapter.models import ScannerReferenceData


class ScannerReferenceStore:
    def __init__(
        self,
        values: Iterable[ScannerReferenceData] = (),
    ) -> None:
        self._values: dict[str, ScannerReferenceData] = {}
        self.update_many(values)

    def put(self, value: ScannerReferenceData) -> None:
        self._values[value.symbol] = value

    def update_many(
        self,
        values: Iterable[ScannerReferenceData],
    ) -> None:
        for value in values:
            self.put(value)

    def get(self, symbol: str) -> ScannerReferenceData | None:
        return self._values.get(symbol.strip().upper())

    def remove(self, symbol: str) -> None:
        self._values.pop(symbol.strip().upper(), None)

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))

    def __len__(self) -> int:
        return len(self._values)
