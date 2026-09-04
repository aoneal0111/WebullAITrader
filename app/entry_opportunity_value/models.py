"""Immutable point-in-time contracts for entry-value research.

These types deliberately contain no command, gateway, broker, account, or risk
authority.  An observation describes a counterfactual; it can never authorize
or mutate an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ComponentAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNCALIBRATED = "UNCALIBRATED"
    UNAVAILABLE = "UNAVAILABLE"


class ShadowAction(StrEnum):
    KEEP_ORIGINAL_LIMIT = "SHADOW_KEEP_ORIGINAL_LIMIT"
    ABANDON_PRICE_DRIFT = "SHADOW_ABANDON_PRICE_DRIFT"
    ABANDON_RISK_GEOMETRY = "SHADOW_ABANDON_RISK_GEOMETRY"
    ABANDON_STALE = "SHADOW_ABANDON_STALE"
    WAIT_FOR_RETRACE = "SHADOW_WAIT_FOR_RETRACE"
    REPRICE_CANDIDATE = "SHADOW_REPRICE_CANDIDATE"
    INSUFFICIENT_EVIDENCE = "SHADOW_INSUFFICIENT_EVIDENCE"


class OpportunityTrend(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    CONFIDENCE_UP_ENTRY_VALUE_DOWN = "CONFIDENCE_UP_ENTRY_VALUE_DOWN"
    CONFIDENCE_UP_ENTRY_VALUE_UP = "CONFIDENCE_UP_ENTRY_VALUE_UP"
    CONFIDENCE_DOWN_ENTRY_VALUE_UP = "CONFIDENCE_DOWN_ENTRY_VALUE_UP"
    CONFIDENCE_DOWN_ENTRY_VALUE_DOWN = "CONFIDENCE_DOWN_ENTRY_VALUE_DOWN"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class EntryOpportunityValueInput:
    """Information available no later than one decision cutoff."""

    symbol: str
    decision_cutoff: datetime
    environment: str
    session: str
    strategy: str
    setup: str
    lifecycle_id: str
    opportunity_id: str | None
    entry_plan_at: datetime
    entry_ready_at: datetime
    planned_entry_price: Decimal
    structural_stop: Decimal
    planned_quantity: int
    setup_detected_at: datetime | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    quote_timestamp: datetime | None = None
    quote_received_at: datetime | None = None
    best_bid_size: Decimal | None = None
    best_ask_size: Decimal | None = None
    scanner_rank: int | None = None
    scanner_score: Decimal | None = None
    percentage_change: Decimal | None = None
    relative_volume: Decimal | None = None
    detector_memberships: tuple[str, ...] = ()
    technical_state: str | None = None
    entry_ready_state: str | None = None
    valid_until: datetime | None = None
    day_boundary: datetime | None = None
    order_terminal_state: str | None = None
    technical_confidence: Decimal | None = None
    continuation_probability: Decimal | None = None
    continuation_probability_basis: str | None = None
    continuation_probability_observed_at: datetime | None = None
    expected_remaining_move: Decimal | None = None
    expected_remaining_move_basis: str | None = None
    expected_remaining_move_observed_at: datetime | None = None
    expected_downside: Decimal | None = None
    expected_downside_basis: str | None = None
    expected_downside_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized = self.symbol.strip().upper()
        if not normalized or not all(
            value.strip()
            for value in (
                self.environment,
                self.session,
                self.strategy,
                self.setup,
                self.lifecycle_id,
            )
        ):
            raise ValueError("entry opportunity identity is required")
        object.__setattr__(self, "symbol", normalized)
        for name in ("decision_cutoff", "entry_plan_at", "entry_ready_at"):
            _aware(getattr(self, name), name)
            if getattr(self, name) > self.decision_cutoff:
                raise ValueError(f"{name} cannot exceed decision cutoff")
        if self.setup_detected_at is not None:
            _aware(self.setup_detected_at, "setup_detected_at")
            if self.setup_detected_at > self.decision_cutoff:
                raise ValueError("setup_detected_at cannot exceed decision cutoff")
        for name in ("quote_timestamp", "quote_received_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
                if value > self.decision_cutoff:
                    raise ValueError(f"{name} cannot exceed decision cutoff")
        for name in ("valid_until", "day_boundary"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
        if self.planned_entry_price <= 0 or self.structural_stop <= 0:
            raise ValueError("entry and structural stop must be positive")
        if self.planned_quantity <= 0:
            raise ValueError("planned quantity must be positive")
        for name in ("bid", "ask", "last"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        for name in ("best_bid_size", "best_ask_size"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.continuation_probability is not None and not (
            Decimal("0") <= self.continuation_probability <= Decimal("1")
        ):
            raise ValueError("continuation probability must be within [0,1]")
        if (self.continuation_probability is None) != (
            self.continuation_probability_basis is None
        ):
            raise ValueError("continuation probability requires explicit provenance")
        _validate_estimate_time(
            self.continuation_probability,
            self.continuation_probability_observed_at,
            self.decision_cutoff,
            "continuation_probability",
        )
        if self.expected_remaining_move is not None and self.expected_remaining_move < 0:
            raise ValueError("expected remaining move cannot be negative")
        if (self.expected_remaining_move is None) != (
            self.expected_remaining_move_basis is None
        ):
            raise ValueError("expected remaining move requires explicit provenance")
        _validate_estimate_time(
            self.expected_remaining_move,
            self.expected_remaining_move_observed_at,
            self.decision_cutoff,
            "expected_remaining_move",
        )
        if self.expected_downside is not None and self.expected_downside < 0:
            raise ValueError("expected downside cannot be negative")
        if (self.expected_downside is None) != (self.expected_downside_basis is None):
            raise ValueError("expected downside requires explicit provenance")
        _validate_estimate_time(
            self.expected_downside,
            self.expected_downside_observed_at,
            self.decision_cutoff,
            "expected_downside",
        )
        memberships = tuple(sorted({item.strip().upper() for item in self.detector_memberships if item.strip()}))
        object.__setattr__(self, "detector_memberships", memberships)


@dataclass(frozen=True, slots=True)
class OpportunityComponent:
    name: str
    value: Decimal | None
    availability: ComponentAvailability
    reason: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.reason.strip():
            raise ValueError("component name and reason are required")
        if self.availability is ComponentAvailability.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable component cannot carry a value")


@dataclass(frozen=True, slots=True)
class PriceDriftFeatures:
    original_risk_per_share: Decimal | None
    absolute_price_drift: Decimal | None
    price_drift_percent: Decimal | None
    price_drift_in_r: Decimal | None
    current_entry_risk_per_share: Decimal | None
    current_entry_risk_multiple_vs_original: Decimal | None
    distance_from_trigger: Decimal | None
    distance_from_stop: Decimal | None
    spread_cost_per_share: Decimal | None
    spread_percent: Decimal | None
    spread_cost_in_r: Decimal | None
    estimated_round_trip_top_of_book_cost: Decimal | None
    quote_age_ms: Decimal | None
    time_since_entry_plan_ms: int
    time_since_entry_ready_ms: int
    setup_age_ms: int | None
    remaining_validity_ms: int | None
    original_limit_marketable: bool | None
    distance_to_market: Decimal | None
    current_risk_budget_quantity: int | None
    would_best_ask_entry_change_risk_budget: bool | None
    would_original_quantity_violate_risk_at_ask: bool | None


@dataclass(frozen=True, slots=True)
class BoundedRepriceCandidate:
    derivation: str
    entry_price: Decimal
    structural_stop: Decimal
    risk_per_share: Decimal
    unchanged_risk_budget_quantity: int
    reward_risk: Decimal | None
    execution_authorized: bool = False
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.execution_authorized or not self.research_only:
            raise ValueError("reprice candidates are research-only")


@dataclass(frozen=True, slots=True)
class EntryOpportunityValueObservation:
    observation_id: str
    context: EntryOpportunityValueInput
    features: PriceDriftFeatures
    components: tuple[OpportunityComponent, ...]
    shadow_action: ShadowAction
    action_reason: str
    opportunity_trend: OpportunityTrend
    reprice_candidates: tuple[BoundedRepriceCandidate, ...]
    created_at: datetime
    research_only: bool = True
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        _aware(self.created_at, "created_at")
        if not self.observation_id or not self.research_only or self.execution_authorized:
            raise ValueError("shadow observations can never carry execution authority")
        if self.created_at < self.context.decision_cutoff:
            raise ValueError("observation creation cannot precede decision cutoff")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_estimate_time(value, observed_at, cutoff, name) -> None:
    if (value is None) != (observed_at is None):
        raise ValueError(f"{name} requires a point-in-time evidence timestamp")
    if observed_at is not None:
        _aware(observed_at, f"{name}_observed_at")
        if observed_at > cutoff:
            raise ValueError(f"{name} evidence cannot exceed decision cutoff")


__all__ = [
    "BoundedRepriceCandidate",
    "ComponentAvailability",
    "EntryOpportunityValueInput",
    "EntryOpportunityValueObservation",
    "OpportunityComponent",
    "OpportunityTrend",
    "PriceDriftFeatures",
    "ShadowAction",
]
