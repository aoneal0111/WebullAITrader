from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.momentum_scanner.models import ScannerDecision


@dataclass(frozen=True, slots=True)
class ReferenceWarmupFailure:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    timestamp: datetime
    active_symbols: tuple[str, ...]
    decisions: tuple[ScannerDecision, ...]
    ranked_candidates: tuple[ScannerDecision, ...]
    processed_events: int
    ignored_events: int
    reference_failures: tuple[ReferenceWarmupFailure, ...]

    @property
    def qualified_count(self) -> int:
        return len(self.ranked_candidates)

    @property
    def decision_count(self) -> int:
        return len(self.decisions)
