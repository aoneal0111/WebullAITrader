from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.momentum_scanner.models import ScannerDecision
from app.reference_data.models import ReferenceRecord


@dataclass(frozen=True, slots=True)
class ReferenceWarmupFailure:
    symbol: str
    reason: str
    failure_type: str = "temporary"
    stage: str = "reference_warmup"
    environment: str = "UNKNOWN"
    endpoint: str = "stock_bars"
    retryable: bool = True

    @property
    def event_type(self) -> str:
        return (
            "symbol_rejected"
            if self.failure_type == "unsupported_symbol"
            else "reference_warmup_failed"
        )


@dataclass(frozen=True, slots=True)
class ReferenceWarmupResult:
    active_symbols: tuple[str, ...] = ()
    unsupported_rejections: tuple[ReferenceWarmupFailure, ...] = ()
    temporary_failures: tuple[ReferenceWarmupFailure, ...] = ()
    missing_data_failures: tuple[ReferenceWarmupFailure, ...] = ()
    successful_records: tuple[ReferenceRecord, ...] = ()

    @property
    def failures(self) -> tuple[ReferenceWarmupFailure, ...]:
        return (
            self.unsupported_rejections
            + self.temporary_failures
            + self.missing_data_failures
        )


@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    timestamp: datetime
    active_symbols: tuple[str, ...]
    decisions: tuple[ScannerDecision, ...]
    ranked_candidates: tuple[ScannerDecision, ...]
    processed_events: int
    ignored_events: int
    reference_failures: tuple[ReferenceWarmupFailure, ...]
    session: str = "UNKNOWN"
    warmup_result: ReferenceWarmupResult = ReferenceWarmupResult()
    healthy: bool = True
    health_reason: str | None = None

    @property
    def qualified_count(self) -> int:
        return len(self.ranked_candidates)

    @property
    def decision_count(self) -> int:
        return len(self.decisions)
