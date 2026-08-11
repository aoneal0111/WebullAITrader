"""Thread-safe GUI state for an explicit chart inspection selection."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class ChartInspectionState:
    """Operator intent that is independent of scanner candidate membership."""

    symbol: str | None = None
    revision: int = 0


class ChartInspectionStore:
    """Own immutable inspection snapshots across GUI/scanner refresh threads."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state = ChartInspectionState()

    def snapshot(self) -> ChartInspectionState:
        with self._lock:
            return self._state

    def select(self, symbol: str) -> ChartInspectionState:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("inspection symbol is required")
        normalized = symbol.strip().upper()
        with self._lock:
            if normalized == self._state.symbol:
                return self._state
            self._state = ChartInspectionState(
                symbol=normalized,
                revision=self._state.revision + 1,
            )
            return self._state

    def clear(self) -> ChartInspectionState:
        with self._lock:
            if self._state.symbol is None:
                return self._state
            self._state = ChartInspectionState(
                revision=self._state.revision + 1,
            )
            return self._state


__all__ = ["ChartInspectionState", "ChartInspectionStore"]
