"""Bounded near-miss aggregation suitable for periodic event publication."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

from .models import MomentumCandidate, ReasonCode


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    rejection_counts: tuple[tuple[ReasonCode, int], ...]
    recent_symbols: tuple[str, ...]


class BoundedTelemetry:
    def __init__(self, symbol_limit: int = 10) -> None:
        if symbol_limit <= 0:
            raise ValueError("symbol_limit must be positive")
        self._counts: Counter[ReasonCode] = Counter()
        self._symbols: deque[str] = deque(maxlen=symbol_limit)

    def observe(self, candidate: MomentumCandidate) -> None:
        self._counts.update(candidate.reason_codes)
        self._symbols.append(candidate.symbol)

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(tuple(sorted(self._counts.items(), key=lambda item: item[0].value)), tuple(self._symbols))


__all__ = ["TelemetrySnapshot", "BoundedTelemetry"]
