"""Immutable research facts for scanner-universe admission forensics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class UniverseAdmissionStage(StrEnum):
    REFRESH_STARTED = "REFRESH_STARTED"
    SCREENER_RETURNED = "SCREENER_RETURNED"
    REQUEST_WINDOW_INCLUDED = "REQUEST_WINDOW_INCLUDED"
    SOURCE_DEDUPLICATED = "SOURCE_DEDUPLICATED"
    INSTRUMENT_LOOKUP_FAILED = "INSTRUMENT_LOOKUP_FAILED"
    NORMALIZATION_STARTED = "NORMALIZATION_STARTED"
    SYMBOL_NORMALIZED = "SYMBOL_NORMALIZED"
    NORMALIZATION_REJECTED = "NORMALIZATION_REJECTED"
    UNIVERSE_FILTER_ACCEPTED = "UNIVERSE_FILTER_ACCEPTED"
    UNIVERSE_FILTER_REJECTED = "UNIVERSE_FILTER_REJECTED"
    REFERENCE_WARMUP_STARTED = "REFERENCE_WARMUP_STARTED"
    REFERENCE_WARMUP_ACCEPTED = "REFERENCE_WARMUP_ACCEPTED"
    REFERENCE_WARMUP_REJECTED = "REFERENCE_WARMUP_REJECTED"
    UNIVERSE_ADMITTED = "UNIVERSE_ADMITTED"
    SCANNER_EVALUATION_REACHED = "SCANNER_EVALUATION_REACHED"


class UniverseAdmissionOutcome(StrEnum):
    STARTED = "STARTED"
    OBSERVED = "OBSERVED"
    INCLUDED = "INCLUDED"
    UNIQUE = "UNIQUE"
    MERGED = "MERGED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    REACHED = "REACHED"


@dataclass(frozen=True, slots=True)
class UniverseAdmissionEvent:
    schema_version: int
    event_id: str
    refresh_id: str
    timestamp: datetime
    session: str
    trading_date: date
    provider: str
    screener_identity: str | None
    source_rank: int | None
    raw_symbol: str | None
    normalized_symbol: str | None
    stage: UniverseAdmissionStage
    outcome: UniverseAdmissionOutcome
    reason: str
    upstream_fields_json: str
    research_only: bool = True
    selection_authorized: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported universe telemetry schema")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("universe telemetry timestamp must be timezone-aware")
        if not self.event_id or not self.refresh_id:
            raise ValueError("universe telemetry identities are required")
        if self.source_rank is not None and self.source_rank < 1:
            raise ValueError("source rank must be positive")
        if not self.reason:
            raise ValueError("universe telemetry reason is required")
        if not self.research_only or self.selection_authorized or self.execution_authorized:
            raise ValueError("universe telemetry must remain non-authoritative research")


@dataclass(frozen=True, slots=True)
class UniverseAdmissionMetrics:
    enabled: bool
    healthy: bool
    accepted: int
    completed: int
    suppressed: int
    rejected: int
    failed: int
    outstanding: int
    queue_depth: int
    queue_high_water: int
    refresh_count: int
    persistence_path: str
    stopped: bool
    last_error_type: str | None


__all__ = [
    "UniverseAdmissionEvent",
    "UniverseAdmissionMetrics",
    "UniverseAdmissionOutcome",
    "UniverseAdmissionStage",
]
