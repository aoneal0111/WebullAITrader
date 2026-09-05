"""Immutable contracts for adaptive working-entry research.

Nothing in this package is an order command.  Inputs contain only facts whose
timestamps are at or before ``decision_cutoff`` and outputs are permanently
marked as non-authoritative research.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class MaterialChangeReason(StrEnum):
    PRICE_DISPLACEMENT = "PRICE_DISPLACEMENT"
    QUOTE_DISPLACEMENT = "QUOTE_DISPLACEMENT"
    SPREAD_CHANGE = "SPREAD_CHANGE"
    MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"
    VOLUME_ACCELERATION = "VOLUME_ACCELERATION"
    REFERENCE_PRICE_CHANGED = "REFERENCE_PRICE_CHANGED"
    STRUCTURAL_STOP_CHANGED = "STRUCTURAL_STOP_CHANGED"
    SETUP_STATE_CHANGED = "SETUP_STATE_CHANGED"
    TECHNICAL_ACTIONABILITY_CHANGED = "TECHNICAL_ACTIONABILITY_CHANGED"
    ORDER_NEARING_EXPIRY = "ORDER_NEARING_EXPIRY"
    ORDER_TERMINATED = "ORDER_TERMINATED"


class ShadowRecommendation(StrEnum):
    KEEP_ORIGINAL_LIMIT = "KEEP_ORIGINAL_LIMIT"
    WAIT_FOR_RETRACE = "WAIT_FOR_RETRACE"
    REPRICE_AND_RESIZE_CANDIDATE = "REPRICE_AND_RESIZE_CANDIDATE"
    ABANDON_PRICE_DRIFT = "ABANDON_PRICE_DRIFT"
    ABANDON_RISK_GEOMETRY = "ABANDON_RISK_GEOMETRY"
    ABANDON_SETUP_INVALIDATED = "ABANDON_SETUP_INVALIDATED"
    ABANDON_STALE = "ABANDON_STALE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True, slots=True)
class WorkingEntrySnapshot:
    schema_version: str
    market_event_at: datetime
    observed_at: datetime
    decision_cutoff: datetime
    environment: str
    symbol: str
    strategy_id: str
    strategy_version: str
    strategy_lifecycle_id: str
    setup_type: str
    setup_state: str | None
    order_id: str
    side: str
    order_type: str
    order_status: str
    original_limit_price: Decimal
    original_quantity: int
    remaining_quantity: int
    filled_quantity: int
    original_structural_stop: Decimal
    original_risk_per_share: Decimal
    original_total_risk: Decimal
    order_submitted_at: datetime
    order_state_at: datetime
    entry_valid_until: datetime
    working_age_seconds: Decimal
    remaining_validity_seconds: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    quote_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    warrior_evidence_at: datetime | None = None
    position_evidence_at: datetime | None = None
    quote_freshness_seconds: Decimal | None = None
    spread: Decimal | None = None
    spread_percent: Decimal | None = None
    scanner_rank: int | None = None
    scanner_score: Decimal | None = None
    relative_volume: Decimal | None = None
    percentage_change: Decimal | None = None
    volume: Decimal | None = None
    dollar_volume: Decimal | None = None
    float_shares: Decimal | None = None
    warrior_current_state: str | None = None
    current_reference_price: Decimal | None = None
    current_structural_stop: Decimal | None = None
    current_setup_quality: Decimal | None = None
    current_technical_actionable: bool | None = None
    existing_position_quantity: int = 0
    momentum_velocity: Decimal | None = None
    volume_acceleration: Decimal | None = None
    distance_from_hod_percent: Decimal | None = None
    unavailable_evidence: tuple[str, ...] = ()
    terminal_reason: str | None = None
    research_only: bool = True
    execution_authorized: bool = False
    production_promoted: bool = False

    def __post_init__(self) -> None:
        for name in (
            "market_event_at",
            "observed_at",
            "decision_cutoff",
            "order_submitted_at",
            "order_state_at",
            "entry_valid_until",
        ):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.observed_at > self.decision_cutoff:
            raise ValueError("observed_at cannot exceed decision_cutoff")
        for name in (
            "market_event_at",
            "order_submitted_at",
            "order_state_at",
            "quote_timestamp",
            "last_timestamp",
            "warrior_evidence_at",
            "position_evidence_at",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
            if value > self.decision_cutoff:
                raise ValueError(f"{name} cannot exceed decision_cutoff")
        if self.order_state_at < self.order_submitted_at:
            raise ValueError("order_state_at cannot precede order_submitted_at")
        if not self.symbol.strip() or not self.order_id.strip() or not self.strategy_lifecycle_id.strip():
            raise ValueError("working-entry identity is required")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.environment.strip().upper() not in {"TEST", "PAPER", "SANDBOX"}:
            raise ValueError("LIVE and unknown environments are ineligible")
        if self.original_limit_price <= 0 or self.original_structural_stop <= 0:
            raise ValueError("original entry geometry must be positive")
        if self.original_risk_per_share <= 0 or self.original_total_risk <= 0:
            raise ValueError("original risk geometry must be positive")
        if min(self.original_quantity, self.remaining_quantity, self.filled_quantity, self.existing_position_quantity) < 0:
            raise ValueError("quantities cannot be negative")
        if self.remaining_quantity + self.filled_quantity != self.original_quantity:
            raise ValueError("remaining plus filled quantity must equal original quantity")
        if not self.research_only or self.execution_authorized or self.production_promoted:
            raise ValueError("working-entry snapshots are research-only")
        object.__setattr__(self, "unavailable_evidence", tuple(sorted(set(self.unavailable_evidence))))


@dataclass(frozen=True, slots=True)
class EntryPlan:
    entry: Decimal | None
    stop: Decimal | None
    quantity: int | None
    risk_per_share: Decimal | None
    total_risk: Decimal | None


@dataclass(frozen=True, slots=True)
class AdaptiveEntryRecommendation:
    recommendation_id: str
    schema_version: str
    observed_at: datetime
    decision_cutoff: datetime
    symbol: str
    order_id: str
    strategy_lifecycle_id: str
    material_change_reasons: tuple[MaterialChangeReason, ...]
    recommendation: ShadowRecommendation
    original: EntryPlan
    fresh_hypothetical: EntryPlan
    price_drift: Decimal | None
    price_drift_percent: Decimal | None
    price_drift_r: Decimal | None
    risk_inflation_ratio: Decimal | None
    fresh_entry_delta: Decimal | None
    fresh_stop_delta: Decimal | None
    fresh_quantity_delta: int | None
    fresh_risk_per_share_delta: Decimal | None
    fresh_total_risk_delta: Decimal | None
    original_limit_to_bid: Decimal | None
    original_limit_to_ask: Decimal | None
    fresh_entry_to_bid: Decimal | None
    fresh_entry_to_ask: Decimal | None
    remaining_validity_seconds: Decimal
    existing_position_quantity: int
    remaining_order_quantity: int
    evidence_codes: tuple[str, ...]
    unavailable_evidence: tuple[str, ...]
    estimated_friction_per_share: Decimal | None
    research_only: bool = True
    execution_authorized: bool = False
    production_promoted: bool = False

    def __post_init__(self) -> None:
        if not self.recommendation_id:
            raise ValueError("recommendation_id is required")
        if not self.research_only or self.execution_authorized or self.production_promoted:
            raise ValueError("adaptive recommendations can never authorize execution")


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    outcome_id: str
    recommendation_id: str
    decision_cutoff: datetime
    horizon_seconds: int
    observed_at: datetime
    future_price: Decimal
    mfe: Decimal | None
    mae: Decimal | None
    original_limit_hypothetically_fillable: bool | None
    fresh_entry_hypothetically_fillable: bool | None
    fill_model: str
    labels_only: bool = True
    research_only: bool = True
    execution_authorized: bool = False
    production_promoted: bool = False
    # Schema-v2 immutable provenance.  Defaults keep older in-memory callers
    # and historical JSONL rows readable; new labels always populate these.
    schema_version: str = "2"
    symbol: str = ""
    order_id: str = ""
    recommendation: str = ""
    strategy_id: str = ""
    strategy_lifecycle_id: str = ""
    setup_type: str = ""
    original_entry: Decimal | None = None
    original_stop: Decimal | None = None
    original_quantity: int | None = None
    original_risk_per_share: Decimal | None = None
    original_total_risk: Decimal | None = None
    fresh_entry: Decimal | None = None
    fresh_stop: Decimal | None = None
    fresh_quantity: int | None = None

    def __post_init__(self) -> None:
        if self.observed_at <= self.decision_cutoff:
            raise ValueError("outcomes must be observed after the decision cutoff")
        if not self.labels_only or not self.research_only or self.execution_authorized or self.production_promoted:
            raise ValueError("outcomes are research-only labels")


__all__ = ["AdaptiveEntryRecommendation", "EntryPlan", "MaterialChangeReason", "OutcomeObservation", "ShadowRecommendation", "WorkingEntrySnapshot"]
