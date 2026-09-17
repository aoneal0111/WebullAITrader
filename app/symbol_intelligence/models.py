"""Immutable, non-authoritative symbol-intelligence domain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .security import freeze_metadata, sanitize_source_reference, validate_safe_text


FACT_SCHEMA_VERSION = 1
DERIVATION_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1

FACT_METADATA_KEYS = frozenset({
    "accession_number", "exchange", "external_reference", "filing_date",
    "form", "item_codes", "primary_document", "provider_status",
    "report_date", "security_class", "details",
})
DERIVATION_METADATA_KEYS = frozenset({
    "basis", "decay_bucket", "evidence_class", "feature_versions",
    "limitations", "model_family",
})


class AttentionState(StrEnum):
    """Observational priority only; this enum grants no economic authority."""

    ARCHIVE = "ARCHIVE"
    BACKGROUND = "BACKGROUND"
    WATCH = "WATCH"
    ELEVATED = "ELEVATED"
    HIGH_PRIORITY = "HIGH_PRIORITY"
    ACTIVE_OPPORTUNITY = "ACTIVE_OPPORTUNITY"


ATTENTION_RANK = {state: rank for rank, state in enumerate(AttentionState)}


class EventType(StrEnum):
    SEC_FILING = "SEC_FILING"
    MATERIAL_AGREEMENT = "MATERIAL_AGREEMENT"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    FINANCING = "FINANCING"
    OFFERING_DILUTION = "OFFERING_DILUTION"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    LISTING_EXCHANGE = "LISTING_EXCHANGE"
    BANKRUPTCY_DISTRESS = "BANKRUPTCY_DISTRESS"
    CLINICAL_FDA = "CLINICAL_FDA"
    GOVERNMENT_CONTRACT = "GOVERNMENT_CONTRACT"
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    LEADERSHIP = "LEADERSHIP"
    HALT = "HALT"
    OTHER_MATERIAL = "OTHER_MATERIAL"


class DecayClass(StrEnum):
    FLASH = "FLASH"
    SESSION = "SESSION"
    MULTI_DAY = "MULTI_DAY"
    STRUCTURAL = "STRUCTURAL"
    RISK_PERSISTENT = "RISK_PERSISTENT"


class Direction(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class SourceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class SecIssuerAcquisitionStatus(StrEnum):
    """Durable completeness state for one SEC issuer observation."""

    NEVER_ACQUIRED = "NEVER_ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class SecManualTargetStatus(StrEnum):
    """Bounded outcomes for explicit operator target admission."""

    ACCEPTED = "ACCEPTED"
    DEDUPLICATED = "DEDUPLICATED"
    INVALID = "INVALID"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_READY = "NOT_READY"
    REJECTED_LIMIT = "REJECTED_LIMIT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    QUEUE_REJECTED = "QUEUE_REJECTED"


@dataclass(frozen=True, slots=True)
class SecManualTargetEntry:
    """One bounded target outcome; at most three are returned per call."""

    symbol: str
    status: SecManualTargetStatus

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol or len(self.symbol) > 32:
            raise ValueError("manual target symbol is malformed or exceeds bound")
        if not isinstance(self.status, SecManualTargetStatus):
            raise TypeError("manual target status must be bounded")


@dataclass(frozen=True, slots=True)
class SecManualTargetEnqueueResult:
    """Immutable aggregate result for one manual target admission call."""

    requested: int = 0
    accepted: int = 0
    deduplicated: int = 0
    invalid: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    not_ready: int = 0
    rejected_limit: int = 0
    service_unavailable: int = 0
    queue_rejected: int = 0
    entries: tuple[SecManualTargetEntry, ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.requested, self.accepted, self.deduplicated, self.invalid,
            self.unresolved, self.ambiguous, self.not_ready,
            self.rejected_limit, self.service_unavailable, self.queue_rejected,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("manual target counters must be bounded non-negative integers")
        if len(self.entries) > 3:
            raise ValueError("manual target entry details exceed bound")
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class SecManualTargetDiagnostics:
    """Bounded per-composition target admission diagnostics."""

    ticker_map_ready: bool
    ticker_map_last_success_at: datetime | None
    target_limit: int
    target_unique_issuers_admitted: int
    requests: int
    requested_symbols: int
    accepted: int
    deduplicated: int
    invalid: int
    unresolved: int
    ambiguous: int
    not_ready: int
    rejected_limit: int
    service_unavailable: int
    queue_rejected: int


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _optional_text(value: str | None, field_name: str, maximum: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return validate_safe_text(str(value), field=field_name, maximum=maximum)


def _identifier(value: str, field_name: str, maximum: int = 256) -> str:
    return validate_safe_text(value, field=field_name, maximum=maximum)


@dataclass(frozen=True, slots=True)
class IntelligenceEvent:
    """A normalized source fact. Directional interpretation belongs elsewhere."""

    event_id: str
    symbol: str
    event_type: EventType
    event_subtype: str
    source: str
    published_at: datetime
    observed_at: datetime
    verified: bool
    decay_class: DecayClass
    source_parser_version: str
    issuer_id: str | None = None
    source_id: str | None = None
    effective_at: datetime | None = None
    headline: str | None = None
    summary: str | None = None
    source_reference: str | None = None
    supersedes_event_id: str | None = None
    fact_schema_version: int = FACT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.fact_schema_version != FACT_SCHEMA_VERSION:
            raise ValueError("unsupported fact schema version")
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "symbol", _identifier(self.symbol.upper(), "symbol", 32))
        object.__setattr__(self, "event_subtype", _identifier(self.event_subtype, "event_subtype", 128))
        object.__setattr__(self, "source", _identifier(self.source.upper(), "source", 64))
        object.__setattr__(self, "source_parser_version", _identifier(self.source_parser_version, "source_parser_version", 64))
        object.__setattr__(self, "issuer_id", _optional_text(self.issuer_id, "issuer_id", 128))
        object.__setattr__(self, "source_id", _optional_text(self.source_id, "source_id", 256))
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.observed_at < self.published_at:
            raise ValueError("observed_at cannot precede published_at")
        if self.effective_at is not None:
            object.__setattr__(self, "effective_at", _utc(self.effective_at, "effective_at"))
        object.__setattr__(self, "headline", _optional_text(self.headline, "headline", 512))
        object.__setattr__(self, "summary", _optional_text(self.summary, "summary", 2_048))
        object.__setattr__(self, "source_reference", sanitize_source_reference(self.source_reference))
        object.__setattr__(self, "supersedes_event_id", _optional_text(self.supersedes_event_id, "supersedes_event_id", 256))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata, allowed_keys=FACT_METADATA_KEYS))


@dataclass(frozen=True, slots=True)
class IntelligenceDerivation:
    """A versioned inference which cannot overwrite its supporting facts."""

    derivation_id: str
    symbol: str
    derivation_type: str
    direction: Direction
    significance: int
    confidence: Decimal
    algorithm_id: str
    algorithm_version: str
    generated_at: datetime
    fact_cutoff: datetime
    supporting_event_ids: tuple[str, ...]
    sample_count: int | None = None
    expires_at: datetime | None = None
    stale: bool = False
    derivation_schema_version: int = DERIVATION_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.derivation_schema_version != DERIVATION_SCHEMA_VERSION:
            raise ValueError("unsupported derivation schema version")
        object.__setattr__(self, "derivation_id", _identifier(self.derivation_id, "derivation_id"))
        object.__setattr__(self, "symbol", _identifier(self.symbol.upper(), "symbol", 32))
        object.__setattr__(self, "derivation_type", _identifier(self.derivation_type, "derivation_type", 128))
        object.__setattr__(self, "algorithm_id", _identifier(self.algorithm_id, "algorithm_id", 128))
        object.__setattr__(self, "algorithm_version", _identifier(self.algorithm_version, "algorithm_version", 64))
        if not 0 <= self.significance <= 100:
            raise ValueError("significance must be in 0..100")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be in 0..1")
        if self.sample_count is not None and self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
        object.__setattr__(self, "generated_at", _utc(self.generated_at, "generated_at"))
        object.__setattr__(self, "fact_cutoff", _utc(self.fact_cutoff, "fact_cutoff"))
        if self.fact_cutoff > self.generated_at:
            raise ValueError("fact_cutoff cannot follow generated_at")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at, "expires_at"))
        ids = tuple(dict.fromkeys(_identifier(item, "supporting_event_id") for item in self.supporting_event_ids))
        if not ids or len(ids) > 64:
            raise ValueError("supporting_event_ids must contain 1..64 items")
        object.__setattr__(self, "supporting_event_ids", ids)
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata, allowed_keys=DERIVATION_METADATA_KEYS))


@dataclass(frozen=True, slots=True)
class CatalystSummary:
    event_id: str
    event_type: EventType
    event_subtype: str
    published_at: datetime
    direction: Direction = Direction.UNKNOWN
    significance: int | None = None
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "event_subtype", _identifier(self.event_subtype, "event_subtype", 128))
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        if self.significance is not None and not 0 <= self.significance <= 100:
            raise ValueError("significance must be in 0..100")
        if self.confidence is not None and not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be in 0..1")


@dataclass(frozen=True, slots=True)
class SourceStateSnapshot:
    source: str
    availability: SourceAvailability
    observed_at: datetime
    stale_after: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _identifier(self.source.upper(), "source", 64))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.stale_after is not None:
            object.__setattr__(self, "stale_after", _utc(self.stale_after, "stale_after"))


@dataclass(frozen=True, slots=True)
class SecIssuerAcquisitionState:
    """Bounded, durable SEC submissions completeness for one issuer."""

    issuer_id: str
    status: SecIssuerAcquisitionStatus = SecIssuerAcquisitionStatus.NEVER_ACQUIRED
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_complete_observation_at: datetime | None = None
    next_due_at: datetime | None = None
    failure_category: str | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer_id", _identifier(self.issuer_id, "issuer_id", 128))
        if not isinstance(self.status, SecIssuerAcquisitionStatus):
            object.__setattr__(self, "status", SecIssuerAcquisitionStatus(str(self.status)))
        for name in ("last_attempt_at", "last_success_at", "last_complete_observation_at", "next_due_at", "updated_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name))
        object.__setattr__(self, "failure_category", _optional_text(self.failure_category, "failure_category", 64))
        if self.status is SecIssuerAcquisitionStatus.COMPLETE and self.last_complete_observation_at is None:
            raise ValueError("complete issuer state requires a completion timestamp")
        if self.last_success_at is not None and self.last_complete_observation_at is not None and self.last_success_at < self.last_complete_observation_at:
            raise ValueError("last_success_at cannot precede completion")


@dataclass(frozen=True, slots=True)
class SymbolIntelligenceSnapshot:
    """Immutable attention snapshot with deliberately no execution authority."""

    symbol: str
    as_of: datetime
    generated_at: datetime
    attention_state: AttentionState = AttentionState.ARCHIVE
    priority_score: int = 0
    priority_reasons: tuple[str, ...] = ()
    issuer_id: str | None = None
    issuer_name: str | None = None
    active_catalysts: tuple[CatalystSummary, ...] = ()
    freshest_material_event_id: str | None = None
    catalyst_age_bucket: str | None = None
    catalyst_direction: Direction = Direction.UNKNOWN
    catalyst_significance: int | None = None
    catalyst_confidence: Decimal | None = None
    offering_dilution_risk: str | None = None
    recent_reverse_split_event_id: str | None = None
    recent_material_filing_event_id: str | None = None
    recent_runner_summary: str | None = None
    recent_halt_summary: str | None = None
    recent_opportunity_summary: str | None = None
    source_states: tuple[SourceStateSnapshot, ...] = ()
    fact_cutoff: datetime | None = None
    derivation_versions: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    unknown: bool = False
    stale: bool = False
    snapshot_version: int = SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.snapshot_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported snapshot version")
        object.__setattr__(self, "symbol", _identifier(self.symbol.upper(), "symbol", 32))
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "generated_at", _utc(self.generated_at, "generated_at"))
        if self.fact_cutoff is not None:
            object.__setattr__(self, "fact_cutoff", _utc(self.fact_cutoff, "fact_cutoff"))
            if self.fact_cutoff > self.as_of:
                raise ValueError("fact_cutoff cannot follow snapshot as_of")
        if not 0 <= self.priority_score <= 100:
            raise ValueError("priority_score must be in 0..100")
        catalysts = tuple(self.active_catalysts)
        sources = tuple(self.source_states)
        if any(not isinstance(item, CatalystSummary) for item in catalysts):
            raise TypeError("active_catalysts must contain CatalystSummary values")
        if any(not isinstance(item, SourceStateSnapshot) for item in sources):
            raise TypeError("source_states must contain SourceStateSnapshot values")
        if len(self.priority_reasons) > 32 or len(catalysts) > 64:
            raise ValueError("snapshot collection exceeds its bound")
        if len(sources) > 32 or len(self.derivation_versions) > 64 or len(self.missing_fields) > 64:
            raise ValueError("snapshot collection exceeds its bound")
        object.__setattr__(self, "active_catalysts", catalysts)
        object.__setattr__(self, "source_states", sources)
        object.__setattr__(self, "priority_reasons", tuple(validate_safe_text(item, field="priority_reason", maximum=128) for item in self.priority_reasons))
        object.__setattr__(self, "derivation_versions", tuple(_identifier(item, "derivation_version", 128) for item in self.derivation_versions))
        object.__setattr__(self, "missing_fields", tuple(_identifier(item, "missing_field", 128) for item in self.missing_fields))
        for name in ("issuer_id", "issuer_name", "freshest_material_event_id", "catalyst_age_bucket", "offering_dilution_risk", "recent_reverse_split_event_id", "recent_material_filing_event_id", "recent_runner_summary", "recent_halt_summary", "recent_opportunity_summary"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        if self.catalyst_significance is not None and not 0 <= self.catalyst_significance <= 100:
            raise ValueError("catalyst_significance must be in 0..100")
        if self.catalyst_confidence is not None and not Decimal("0") <= self.catalyst_confidence <= Decimal("1"):
            raise ValueError("catalyst_confidence must be in 0..1")

    @classmethod
    def unknown_snapshot(cls, symbol: str, *, as_of: datetime) -> "SymbolIntelligenceSnapshot":
        timestamp = _utc(as_of, "as_of")
        return cls(
            symbol=symbol,
            as_of=timestamp,
            generated_at=timestamp,
            attention_state=AttentionState.ARCHIVE,
            missing_fields=("all_intelligence",),
            unknown=True,
            stale=True,
        )


@dataclass(frozen=True, slots=True)
class SymbolIdentity:
    issuer_id: str
    canonical_symbol: str
    issuer_name: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer_id", _identifier(self.issuer_id, "issuer_id", 128))
        object.__setattr__(self, "canonical_symbol", _identifier(self.canonical_symbol.upper(), "canonical_symbol", 32))
        object.__setattr__(self, "issuer_name", _optional_text(self.issuer_name, "issuer_name", 256))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))


@dataclass(frozen=True, slots=True)
class SymbolAlias:
    symbol: str
    issuer_id: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _identifier(self.symbol.upper(), "symbol", 32))
        object.__setattr__(self, "issuer_id", _identifier(self.issuer_id, "issuer_id", 128))
        object.__setattr__(self, "valid_from", _utc(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _utc(self.valid_to, "valid_to"))
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must follow valid_from")


@dataclass(frozen=True, slots=True)
class IngestResult:
    inserted: int = 0
    deduplicated: int = 0
    rejected: int = 0
    rejection_reasons: tuple[tuple[str, int], ...] = ()


__all__ = [
    "ATTENTION_RANK", "DERIVATION_SCHEMA_VERSION", "FACT_SCHEMA_VERSION",
    "SNAPSHOT_SCHEMA_VERSION", "AttentionState", "CatalystSummary",
    "DecayClass", "Direction", "EventType", "IngestResult",
    "IntelligenceDerivation", "IntelligenceEvent", "SourceAvailability",
    "SecIssuerAcquisitionState", "SecIssuerAcquisitionStatus", "SourceStateSnapshot", "SymbolAlias", "SymbolIdentity",
    "SymbolIntelligenceSnapshot",
]
