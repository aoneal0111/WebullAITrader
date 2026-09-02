"""Immutable and versioned contracts for autonomous experience memory."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping

SCHEMA_VERSION = 1
FEATURE_VERSION = "ATLAS_DECISION_FEATURES_V1"
PARTITION_POLICY_VERSION = "ATLAS_TEMPORAL_SPLIT_V1"
HORIZONS_MINUTES = (1, 2, 5, 10, 15, 30)
MAX_DECISION_COLLECTION_ITEMS = 64
MAX_TEXT_LENGTH = 512


class AtlasDecision(StrEnum):
    WATCHING = "WATCHING"
    REJECTED = "REJECTED"
    NO_SETUP = "NO_SETUP"
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"
    AWAITING_EXECUTION_DATA = "AWAITING_EXECUTION_DATA"
    ENTRY_READY = "ENTRY_READY"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    FILLED = "FILLED"


class DatasetPartition(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


@dataclass(frozen=True, slots=True)
class ResearchGeneration:
    """Immutable temporal dataset definition for one reproducible study cycle."""

    generation_id: str
    partition_policy_version: str
    training_start: date
    training_end: date
    validation_start: date
    validation_end: date
    holdout_start: date
    holdout_end: date
    evidence_cutoff: date
    feature_version: str
    experience_schema_version: int
    created_at: datetime
    model_version: str | None = None
    policy_version: str | None = None
    predecessor_generation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.generation_id.strip() or not self.partition_policy_version.strip():
            raise ValueError("research generation identity/version is required")
        if not (
            self.training_start <= self.training_end
            < self.validation_start <= self.validation_end
            < self.holdout_start <= self.holdout_end
            <= self.evidence_cutoff
        ):
            raise ValueError("research generation ranges must be ordered and disjoint")
        if self.feature_version != FEATURE_VERSION or self.experience_schema_version != SCHEMA_VERSION:
            raise ValueError("generation lineage is incompatible with this experience schema")
        created = _aware_utc(self.created_at, "generation created_at")
        if self.evidence_cutoff > created.date():
            raise ValueError("generation cannot include evidence from its future")

    def partition_for(self, session_date: date) -> DatasetPartition:
        if self.training_start <= session_date <= self.training_end:
            return DatasetPartition.TRAIN
        if self.validation_start <= session_date <= self.validation_end:
            return DatasetPartition.VALIDATION
        if self.holdout_start <= session_date <= self.holdout_end:
            return DatasetPartition.HOLDOUT
        raise ValueError("experience session is outside frozen generation ranges")


@dataclass(frozen=True, slots=True)
class ResearchGenerationCompletion:
    generation_id: str
    completed_at: datetime
    evaluation_digest: str

    def __post_init__(self) -> None:
        _aware_utc(self.completed_at, "generation completed_at")
        if not self.generation_id.strip() or len(self.evaluation_digest) != 64:
            raise ValueError("completion requires generation identity and SHA-256 evaluation digest")


class OutcomeStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    SESSION_BOUNDARY = "SESSION_BOUNDARY"


class OutcomeKind(StrEnum):
    TECHNICAL_OPPORTUNITY = "TECHNICAL_OPPORTUNITY"
    HYPOTHETICAL_EXECUTION = "HYPOTHETICAL_EXECUTION"
    ACTUAL_PAPER_EXECUTION = "ACTUAL_PAPER_EXECUTION"


class MissedOpportunityClassification(StrEnum):
    PROTECTED_REJECTION = "PROTECTED_REJECTION"
    NEUTRAL_REJECTION = "NEUTRAL_REJECTION"
    PROFITABLE_MISSED_OPPORTUNITY = "PROFITABLE_MISSED_OPPORTUNITY"
    DANGEROUS_FALSE_POSITIVE = "DANGEROUS_FALSE_POSITIVE"
    INSUFFICIENT_OUTCOME_DATA = "INSUFFICIENT_OUTCOME_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExperienceSource(StrEnum):
    RUNTIME = "RUNTIME"
    EXTERNAL_SNAPSHOT_IMPORT = "EXTERNAL_SNAPSHOT_IMPORT"


@dataclass(frozen=True, slots=True)
class OpportunityKey:
    """Stable logical episode key; market event/tick identity is excluded.

    The producer owns ``episode_id`` and must retain it from the first meaningful
    qualification/setup transition until an authoritative reset, terminal state,
    or a distinct setup anchor. Ordinary quote/trade updates therefore map to the
    same experience. Multiple simultaneous setups use different episode IDs.
    """

    strategy_id: str
    symbol: str
    session_date: date
    session: str
    episode_id: str

    def __post_init__(self) -> None:
        if not all((self.strategy_id.strip(), self.symbol.strip(), self.session.strip(), self.episode_id.strip())):
            raise ValueError("opportunity identity fields must be non-empty")

    @property
    def canonical(self) -> str:
        return "|".join((
            self.strategy_id.strip(), self.symbol.strip().upper(),
            self.session_date.isoformat(), self.session.strip().upper(),
            self.episode_id.strip(),
        ))

    @property
    def experience_id(self) -> str:
        return sha256(f"experience-v{SCHEMA_VERSION}|{self.canonical}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DecisionTimeSnapshot:
    """Facts available at or before one explicit decision cutoff.

    Missing values remain ``None`` (UNAVAILABLE); callers must never coerce them
    to zero. ``feature_source_timestamps`` proves every derived feature's cutoff.
    """

    decision_timestamp: datetime
    source_timestamp: datetime | None = None
    last_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_percent: Decimal | None = None
    percentage_change: Decimal | None = None
    current_volume: Decimal | None = None
    average_volume: Decimal | None = None
    relative_volume: Decimal | None = None
    dollar_volume: Decimal | None = None
    float_shares: Decimal | None = None
    tradable: bool | None = None
    halted: bool | None = None
    quote_freshness_seconds: Decimal | None = None
    trade_freshness_seconds: Decimal | None = None
    catalyst_status: str = "UNKNOWN"
    catalyst_type: str | None = None
    catalyst_source_identity: str | None = None
    scanner_qualified: bool | None = None
    scanner_score: Decimal | None = None
    scanner_rank: int | None = None
    passed_rules: tuple[str, ...] = ()
    failed_rules: tuple[str, ...] = ()
    setup_state: str | None = None
    setup_type: str | None = None
    setup_quality: Decimal | None = None
    trigger_price: Decimal | None = None
    structural_stop: Decimal | None = None
    reference_price: Decimal | None = None
    risk_per_share: Decimal | None = None
    setup_timestamp: datetime | None = None
    completed_bar_identity: str | None = None
    features: tuple[tuple[str, Decimal | int | bool | str | None], ...] = ()
    feature_source_timestamps: tuple[tuple[str, datetime], ...] = ()

    def __post_init__(self) -> None:
        cutoff = _aware_utc(self.decision_timestamp, "decision_timestamp")
        for name in ("source_timestamp", "setup_timestamp"):
            value = getattr(self, name)
            if value is not None and _aware_utc(value, name) > cutoff:
                raise ValueError(f"anti-lookahead violation: {name} is after decision cutoff")
        seen: set[str] = set()
        for name, timestamp in self.feature_source_timestamps:
            if name in seen:
                raise ValueError("feature source names must be unique")
            seen.add(name)
            if _aware_utc(timestamp, f"feature source {name}") > cutoff:
                raise ValueError(f"anti-lookahead violation: feature {name} uses future data")
        if self.risk_per_share is not None and self.risk_per_share <= 0:
            raise ValueError("risk_per_share must be positive when available")
        if self.trigger_price is not None and self.structural_stop is not None:
            expected = self.trigger_price - self.structural_stop
            if expected <= 0:
                raise ValueError("trigger must exceed structural stop")
            if self.risk_per_share is not None and expected != self.risk_per_share:
                raise ValueError("risk_per_share must equal trigger minus stop")
        for name in ("passed_rules", "failed_rules", "features", "feature_source_timestamps"):
            if len(getattr(self, name)) > MAX_DECISION_COLLECTION_ITEMS:
                raise ValueError(f"{name} exceeds bounded decision payload limit")
        if len({name for name, _ in self.features}) != len(self.features):
            raise ValueError("feature names must be unique")


@dataclass(frozen=True, slots=True)
class TradeOpportunityExperience:
    key: OpportunityKey
    environment: str
    policy_version: str
    strategy_version: str
    model_version: str
    feature_version: str
    source_event_identity: str
    snapshot: DecisionTimeSnapshot
    atlas_decision: AtlasDecision
    blockers: tuple[str, ...] = ()
    technically_actionable: bool = False
    actually_traded: bool = False
    source: ExperienceSource = ExperienceSource.RUNTIME
    source_store: str | None = None
    source_schema_version: str | None = None
    import_version: str | None = None
    source_record_identity: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.feature_version != FEATURE_VERSION:
            raise ValueError("unsupported experience schema/feature version")
        if not all((self.environment.strip(), self.policy_version.strip(), self.strategy_version.strip(), self.model_version.strip(), self.source_event_identity.strip())):
            raise ValueError("experience lineage fields must be non-empty")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be deduplicated")
        if len(self.blockers) > MAX_DECISION_COLLECTION_ITEMS:
            raise ValueError("blockers exceed bounded decision payload limit")
        text_values = (
            self.environment, self.policy_version, self.strategy_version,
            self.model_version, self.source_event_identity, self.key.episode_id,
        )
        if any(len(value) > MAX_TEXT_LENGTH for value in text_values):
            raise ValueError("experience text exceeds bounded payload limit")
        if self.source is ExperienceSource.EXTERNAL_SNAPSHOT_IMPORT and not all((
            self.source_store, self.source_schema_version, self.import_version,
            self.source_record_identity,
        )):
            raise ValueError("imports require complete reproducibility provenance")

    @property
    def experience_id(self) -> str:
        return self.key.experience_id

    @property
    def partition(self) -> DatasetPartition:
        return temporal_partition(self.key.session_date)


@dataclass(frozen=True, slots=True)
class DecisionObservation:
    """One immutable, meaningful Atlas decision within an experience episode."""

    experience_id: str
    observed_at: datetime
    source_event_identity: str
    atlas_decision: AtlasDecision
    snapshot: DecisionTimeSnapshot
    blockers: tuple[str, ...] = ()
    technically_actionable: bool = False
    actually_traded: bool = False
    symbol: str | None = None
    lifecycle_stage: str = "OBSERVED"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        observed = _aware_utc(self.observed_at, "decision observation timestamp")
        if observed != _aware_utc(self.snapshot.decision_timestamp, "decision cutoff"):
            raise ValueError("decision observation and snapshot cutoffs must match")
        if not self.experience_id.strip() or not self.source_event_identity.strip():
            raise ValueError("decision observation identity is required")
        if not self.lifecycle_stage.strip() or self.schema_version != SCHEMA_VERSION:
            raise ValueError("decision observation version/stage is invalid")
        if self.symbol is not None and not self.symbol.strip():
            raise ValueError("decision observation symbol cannot be blank")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("decision blockers must be deduplicated")

    @property
    def decision_id(self) -> str:
        material = canonical_json({
            "experience_id": self.experience_id,
            "observed_at": self.observed_at,
            "source_event_identity": self.source_event_identity,
            "atlas_decision": self.atlas_decision,
            "lifecycle_stage": self.lifecycle_stage,
        })
        return sha256(f"decision-v{self.schema_version}|{material}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperExecutionObservation:
    """Sanitized PAPER fact. It has no command, broker, or authorization capability."""

    observation_id: str
    observed_at: datetime
    event_type: str
    symbol: str
    experience_id: str | None = None
    correlation_status: str = "UNRESOLVED"
    order_id: str | None = None
    fill_id: str | None = None
    side: str | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None
    strategy_lifecycle_id: str | None = None

    def __post_init__(self) -> None:
        _aware_utc(self.observed_at, "PAPER observation timestamp")
        if not self.observation_id.strip() or not self.event_type.strip() or not self.symbol.strip():
            raise ValueError("PAPER observation identity/type/symbol is required")
        if self.correlation_status not in {"CORRELATED", "UNRESOLVED", "AMBIGUOUS"}:
            raise ValueError("invalid PAPER correlation status")
        if (self.experience_id is not None) != (self.correlation_status == "CORRELATED"):
            raise ValueError("only correlated PAPER facts may name an experience")
        if self.price is not None and self.price <= 0:
            raise ValueError("PAPER observation price must be positive")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("PAPER observation quantity must be positive")


@dataclass(frozen=True, slots=True)
class PriceBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _aware_utc(self.timestamp, "bar timestamp")
        if not self.symbol.strip() or min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("invalid price bar")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("inconsistent OHLC")


@dataclass(frozen=True, slots=True)
class HorizonOutcome:
    experience_id: str
    horizon_minutes: int
    target_timestamp: datetime
    status: OutcomeStatus
    future_price: Decimal | None = None
    return_percent: Decimal | None = None
    mfe: Decimal | None = None
    mae: Decimal | None = None
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    reached_1r: bool | None = None
    reached_2r: bool | None = None
    reached_3r: bool | None = None
    stop_reached: bool | None = None
    time_to_1r_seconds: int | None = None
    time_to_2r_seconds: int | None = None
    time_to_3r_seconds: int | None = None
    time_to_stop_seconds: int | None = None
    first_plan_event: str | None = None
    unavailable_reason: str | None = None
    outcome_as_of: datetime | None = None
    technical_outcome_kind: OutcomeKind = OutcomeKind.TECHNICAL_OPPORTUNITY
    plan_outcome_kind: OutcomeKind | None = None

    def __post_init__(self) -> None:
        if self.horizon_minutes not in HORIZONS_MINUTES:
            raise ValueError("unsupported horizon")
        _aware_utc(self.target_timestamp, "target_timestamp")
        if self.status is OutcomeStatus.COMPLETE and self.future_price is None:
            raise ValueError("complete outcome requires future price")
        if self.technical_outcome_kind is not OutcomeKind.TECHNICAL_OPPORTUNITY:
            raise ValueError("horizon price path must remain a technical outcome")
        has_plan = self.reached_1r is not None
        if has_plan != (self.plan_outcome_kind is OutcomeKind.HYPOTHETICAL_EXECUTION):
            raise ValueError("hypothetical plan kind must exactly match plan fields")


@dataclass(frozen=True, slots=True)
class ActualPaperExecutionOutcome:
    """Actual PAPER facts imported from an authoritative execution identity."""

    experience_id: str
    execution_record_identity: str
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: int
    realized_pnl: Decimal | None
    opened_at: datetime
    closed_at: datetime | None = None
    outcome_kind: OutcomeKind = OutcomeKind.ACTUAL_PAPER_EXECUTION

    def __post_init__(self) -> None:
        if not self.execution_record_identity.strip() or self.entry_price <= 0 or self.quantity <= 0:
            raise ValueError("actual PAPER outcome requires authoritative fill identity")
        _aware_utc(self.opened_at, "opened_at")
        if self.closed_at is not None and _aware_utc(self.closed_at, "closed_at") < _aware_utc(self.opened_at, "opened_at"):
            raise ValueError("execution close cannot precede open")
        if self.outcome_kind is not OutcomeKind.ACTUAL_PAPER_EXECUTION:
            raise ValueError("actual PAPER outcome kind is immutable")


@dataclass(frozen=True, slots=True)
class WorkerMetrics:
    accepted: int
    checkpointed: int
    started: int
    completed: int
    suppressed_duplicate: int
    rejected: int
    failed: int
    outstanding: int
    queue_depth: int
    queue_high_water: int
    pressure_episodes: int
    pressure_recoveries: int
    active_outcomes: int
    accepting: bool
    oldest_work_age_ms: int = 0
    worker_lag_p50_ms: int = 0
    worker_lag_p90_ms: int = 0
    worker_lag_p99_ms: int = 0
    worker_lag_max_ms: int = 0
    experiences_created: int = 0
    decisions_recorded: int = 0
    outcomes_completed: int = 0
    profitable_misses: int = 0
    protected_rejections: int = 0
    discovery_cycles: int = 0
    discovery_detector_evaluations: int = 0
    discovery_raw_firings: int = 0
    discovery_unique_episodes: int = 0
    discovery_normalized_opportunities: int = 0
    discovery_strategy_memberships: int = 0
    discovery_strategy_transitions: int = 0
    discovery_position_correlations: int = 0
    discovery_thesis_observations: int = 0
    discovery_add_on_candidates: int = 0


def temporal_partition(value: date) -> DatasetPartition:
    """Frozen V1 chronological boundaries; an entire session is indivisible.

    V1 is deliberately anchored for the initial dataset. Later research creates
    a new partition-policy version rather than reassigning historical rows.
    """

    if value < date(2026, 7, 1):
        return DatasetPartition.TRAIN
    if value < date(2026, 8, 1):
        return DatasetPartition.VALIDATION
    return DatasetPartition.HOLDOUT


def canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))


def decision_analog_signature(exp: TradeOpportunityExperience) -> str:
    """Canonical explainable signature built exclusively from frozen features."""
    snap = exp.snapshot
    features = dict(snap.features)
    return canonical_json({
        "price_bucket": _bucket(snap.last_price, (2, 5, 10, 20)),
        "change_bucket": _bucket(snap.percentage_change, (5, 10, 20, 50)),
        "rvol_bucket": _bucket(snap.relative_volume, (1, 2, 5, 10)),
        "float_bucket": _bucket(snap.float_shares, (1_000_000, 5_000_000, 20_000_000, 100_000_000)),
        "spread_bucket": _bucket(snap.spread_percent, (Decimal("0.2"), Decimal("0.5"), 1, 2)),
        "setup_type": snap.setup_type, "session": exp.key.session,
        "catalyst_status": snap.catalyst_status,
        "pullback_bucket": _bucket(features.get("pullback_depth_percent"), (1, 2, 4, 8)),
        "distance_hod_bucket": _bucket(features.get("distance_from_hod_percent"), (-8, -4, -2, -1, 0)),
        "setup_state": snap.setup_state,
    })


def experience_payload(value: TradeOpportunityExperience) -> str:
    return canonical_json(asdict(value))


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _aware_utc(value, "serialized timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise TypeError(f"unsupported persisted value: {type(value).__name__}")


def _bucket(value, bounds):
    if value is None:
        return "UNAVAILABLE"
    numeric = Decimal(str(value))
    for bound in bounds:
        if numeric <= Decimal(str(bound)):
            return f"LE_{bound}"
    return f"GT_{bounds[-1]}"
