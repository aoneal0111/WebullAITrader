"""Bounded Warrior opportunity lifecycle between signals and authorization.

The engine owns no broker, account, or market-data ports.  It composes the
point-in-time evidence already produced by Warrior and keeps a technically
valid setup alive while recoverable execution quality changes.  Authoritative
freshness, account, risk, and order services remain independent fail-closed
boundaries.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Callable


class WarriorOpportunityState(StrEnum):
    DISCOVERED = "DISCOVERED"
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"
    ARMED = "ARMED"
    WAITING_EXECUTION = "WAITING_EXECUTION"
    EXECUTABLE = "EXECUTABLE"
    AUTHORIZATION_EVALUATED = "AUTHORIZATION_EVALUATED"
    AUTHORIZED = "AUTHORIZED"
    ORDER_PENDING = "ORDER_PENDING"
    ENTERED = "ENTERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PROTECTED = "PROTECTED"
    MANAGING = "MANAGING"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REJECTED_HARD_SAFETY = "REJECTED_HARD_SAFETY"
    COMPLETED = "COMPLETED"


class AdaptiveOpportunityResult(StrEnum):
    ARMED = "ARMED"
    WAIT = "WAIT"
    EXECUTABLE = "EXECUTABLE"
    STRUCTURALLY_REJECTED = "STRUCTURALLY_REJECTED"


class OpportunityConstraintKind(StrEnum):
    """Classify opportunity evidence by how it may affect execution."""

    ADAPTIVE = "ADAPTIVE"
    HARD_SAFETY = "HARD_SAFETY"
    STRUCTURAL_TERMINAL = "STRUCTURAL_TERMINAL"


class OpportunityReason(StrEnum):
    PRICE_NOT_ELIGIBLE = 'PRICE_NOT_ELIGIBLE'
    EXECUTABLE_TRIGGER_NOT_CONFIRMED = 'EXECUTABLE_TRIGGER_NOT_CONFIRMED'
    TECHNICAL_TRIGGER = "TECHNICAL_TRIGGER"
    EXECUTION_QUALITY_WAIT = "EXECUTION_QUALITY_WAIT"
    PROVIDER_DATA_STALE = "PROVIDER_DATA_STALE"
    PROCESSING_DELAYED = "PROCESSING_DELAYED"
    INVALID_EXECUTION_QUOTE = "INVALID_EXECUTION_QUOTE"
    OBSOLETE_QUOTE_GENERATION = "OBSOLETE_QUOTE_GENERATION"
    EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT = "EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT"
    INSUFFICIENT_CURRENT_REWARD = "INSUFFICIENT_CURRENT_REWARD"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    SETUP_SUPERSEDED = "SETUP_SUPERSEDED"
    OPPORTUNITY_EXPIRED = "OPPORTUNITY_EXPIRED"
    LIFECYCLE_COMPLETED = "LIFECYCLE_COMPLETED"
    SESSION_NOT_ALLOWED = "SESSION_NOT_ALLOWED"
    ACCOUNT_NOT_AUTHORIZED = "ACCOUNT_NOT_AUTHORIZED"
    RISK_NOT_AUTHORIZED = "RISK_NOT_AUTHORIZED"
    DUPLICATE_EXECUTION = "DUPLICATE_EXECUTION"
    PAPER_ORDER_AUTHORIZED = "PAPER_ORDER_AUTHORIZED"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    PAPER_ORDER_PENDING = "PAPER_ORDER_PENDING"
    PAPER_ENTRY_FILLED = "PAPER_ENTRY_FILLED"
    PARTIAL_ENTRY_FILLED = "PARTIAL_ENTRY_FILLED"
    PROTECTION_ACTIVE = "PROTECTION_ACTIVE"
    POSITION_MANAGING = "POSITION_MANAGING"
    EXIT_REQUESTED = "EXIT_REQUESTED"


_ADAPTIVE_REASONS = frozenset({
    OpportunityReason.EXECUTABLE_TRIGGER_NOT_CONFIRMED,
    OpportunityReason.EXECUTION_QUALITY_WAIT,
    OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT,
    OpportunityReason.INSUFFICIENT_CURRENT_REWARD,
})

_RECOVERABLE_HARD_SAFETY_REASONS = frozenset({
    OpportunityReason.PROVIDER_DATA_STALE,
    OpportunityReason.PROCESSING_DELAYED,
    OpportunityReason.INVALID_EXECUTION_QUOTE,
    OpportunityReason.OBSOLETE_QUOTE_GENERATION,
})

_TRUE_HARD_SAFETY_REASONS = frozenset({
    OpportunityReason.PRICE_NOT_ELIGIBLE,
    OpportunityReason.SESSION_NOT_ALLOWED,
    OpportunityReason.ACCOUNT_NOT_AUTHORIZED,
    OpportunityReason.RISK_NOT_AUTHORIZED,
    OpportunityReason.DUPLICATE_EXECUTION,
})

_STRUCTURAL_TERMINAL_REASONS = frozenset({
    OpportunityReason.SETUP_INVALIDATED,
    OpportunityReason.SETUP_SUPERSEDED,
    OpportunityReason.OPPORTUNITY_EXPIRED,
    OpportunityReason.LIFECYCLE_COMPLETED,
})


_TERMINAL_STATES = frozenset({
    WarriorOpportunityState.INVALIDATED,
    WarriorOpportunityState.EXPIRED,
    WarriorOpportunityState.REJECTED_HARD_SAFETY,
    WarriorOpportunityState.COMPLETED,
})


@dataclass(frozen=True, slots=True)
class WarriorOpportunityAssessment:
    """Immutable current-structure and executable-market evidence."""

    symbol: str
    setup_family: str
    generation_id: str
    lifecycle_id: str
    decision_timestamp: datetime
    scanner_timestamp: datetime | None
    quote_timestamp: datetime | None
    last_timestamp: datetime | None
    structural_trigger: Decimal
    executable_entry: Decimal | None
    structural_stop: Decimal
    risk_per_share: Decimal
    target_levels: tuple[Decimal, ...]
    remaining_first_target_r: Decimal | None
    remaining_final_target_r: Decimal | None
    spread_percent: Decimal | None
    execution_cost: Decimal | None
    execution_quality: str
    dollar_liquidity: Decimal
    relative_volume: Decimal
    volatility_context: Decimal | None
    catalyst_context: str
    setup_quality: Decimal
    momentum_priority: Decimal
    displacement_percent: Decimal | None
    adaptive_result: AdaptiveOpportunityResult
    momentum_score: Decimal | None = None
    hard_safety_reason: OpportunityReason | None = None
    reason_codes: tuple[OpportunityReason, ...] = ()
    strategy: str = "WARRIOR_MOMENTUM"
    executable_bid: Decimal | None = None
    provider_last_timestamp: datetime | None = None
    provider_bid_timestamp: datetime | None = None
    provider_ask_timestamp: datetime | None = None
    execution_quote_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.generation_id.strip() or not self.lifecycle_id.strip():
            raise ValueError("opportunity identity is required")
        if self.decision_timestamp.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware")
        if (
            self.hard_safety_reason is None
            and (self.structural_trigger <= 0 or self.structural_stop <= 0)
        ):
            raise ValueError("current structure prices must be positive")
        if (
            self.hard_safety_reason is None
            and (
                self.risk_per_share <= 0
                or self.structural_trigger <= self.structural_stop
            )
        ):
            raise ValueError("current structure requires positive risk")
        if (
            self.hard_safety_reason is None
            and self.executable_entry is not None
            and self.executable_entry <= 0
        ):
            raise ValueError("executable entry must be positive")
        if self.executable_bid is not None and self.executable_bid <= 0:
            raise ValueError("executable bid must be positive")
        for value in (
            self.provider_last_timestamp, self.provider_bid_timestamp,
            self.provider_ask_timestamp, self.execution_quote_timestamp,
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError("provider and execution timestamps must be timezone-aware")

    @property
    def opportunity_id(self) -> str:
        return self.lifecycle_id

    @property
    def strategy_owner(self) -> str:
        return self.strategy


@dataclass(frozen=True, slots=True)
class WarriorOpportunityTransition:
    symbol: str
    generation_id: str
    lifecycle_id: str
    state: WarriorOpportunityState
    occurred_at: datetime
    reason: OpportunityReason


@dataclass(frozen=True, slots=True)
class WarriorOpportunity:
    assessment: WarriorOpportunityAssessment
    state: WarriorOpportunityState
    armed_at: datetime
    updated_at: datetime
    reason: OpportunityReason
    revision: int = 1
    authorization_result: str | None = None
    authorization_reason: str | None = None
    risk_result: str | None = None
    sizing_result: str | None = None
    order_intent_result: str | None = None

    @property
    def opportunity_id(self) -> str:
        return self.assessment.opportunity_id

    @property
    def created_at(self) -> datetime:
        return self.armed_at

    @property
    def last_updated_at(self) -> datetime:
        return self.updated_at

    @property
    def terminal_reason(self) -> OpportunityReason | None:
        return self.reason if self.state in _TERMINAL_STATES else None


@dataclass(frozen=True, slots=True)
class AdaptiveExecutionDecision:
    """One strategy-neutral interpretation of all current opportunity evidence.

    Market-quality and entry-timing imperfections are adaptive constraints.
    They keep a valid opportunity alive for reevaluation instead of allowing
    independent soft gates to terminally reject the lifecycle. Hard account,
    risk, tradability/session, and structural-invalidity reasons remain
    fail-closed.
    """

    state: WarriorOpportunityState
    reason: OpportunityReason
    constraints: tuple[OpportunityReason, ...]
    constraint_kind: OpportunityConstraintKind

    @property
    def executable(self) -> bool:
        return self.state is WarriorOpportunityState.EXECUTABLE


class WarriorOpportunityPolicy:
    """Deterministic comparison policy using existing normalized evidence.

    This intentionally uses a lexicographic key instead of a curve-fitted
    aggregate.  Executability and setup quality lead; momentum, reward,
    liquidity, participation, and cost provide transparent tie-breaks.
    """

    _STATE_ORDER = {
        WarriorOpportunityState.EXECUTABLE: 4,
        WarriorOpportunityState.ARMED: 3,
        WarriorOpportunityState.WAITING_EXECUTION: 2,
        WarriorOpportunityState.TRIGGERED: 1,
    }
    _QUALITY_ORDER = {
        "EXCELLENT": 4,
        "GOOD": 3,
        "MARGINAL": 2,
        "POOR": 1,
        "TEMPORARILY_BLOCKED": 0,
    }

    def rank_key(self, opportunity: WarriorOpportunity) -> tuple[object, ...]:
        value = opportunity.assessment
        reward = value.remaining_final_target_r or Decimal("-1")
        spread = value.spread_percent if value.spread_percent is not None else Decimal("999")
        return (
            self._STATE_ORDER.get(opportunity.state, 0),
            value.setup_quality,
            value.momentum_priority,
            value.momentum_score if value.momentum_score is not None else Decimal("-1"),
            reward,
            self._QUALITY_ORDER.get(value.execution_quality, -1),
            value.dollar_liquidity,
            value.relative_volume,
            -spread,
            value.symbol,
            value.generation_id,
        )

    def execution_decision(
        self, assessment: WarriorOpportunityAssessment,
    ) -> AdaptiveExecutionDecision:
        reasons = tuple(dict.fromkeys(assessment.reason_codes))
        hard = assessment.hard_safety_reason
        if hard is not None:
            if hard in _RECOVERABLE_HARD_SAFETY_REASONS:
                return AdaptiveExecutionDecision(
                    WarriorOpportunityState.WAITING_EXECUTION,
                    hard, reasons or (hard,), OpportunityConstraintKind.HARD_SAFETY,
                )
            return AdaptiveExecutionDecision(
                WarriorOpportunityState.REJECTED_HARD_SAFETY,
                hard, reasons or (hard,), OpportunityConstraintKind.HARD_SAFETY,
            )

        if assessment.adaptive_result is AdaptiveOpportunityResult.EXECUTABLE:
            reason = reasons[-1] if reasons else OpportunityReason.TECHNICAL_TRIGGER
            return AdaptiveExecutionDecision(
                WarriorOpportunityState.EXECUTABLE,
                reason, reasons, OpportunityConstraintKind.ADAPTIVE,
            )

        if assessment.adaptive_result is AdaptiveOpportunityResult.WAIT:
            reason = reasons[-1] if reasons else OpportunityReason.EXECUTION_QUALITY_WAIT
            return AdaptiveExecutionDecision(
                WarriorOpportunityState.WAITING_EXECUTION,
                reason, reasons or (reason,), OpportunityConstraintKind.ADAPTIVE,
            )

        if assessment.adaptive_result is AdaptiveOpportunityResult.STRUCTURALLY_REJECTED:
            reason = reasons[-1] if reasons else OpportunityReason.SETUP_INVALIDATED
            # A producer may have historically labelled a soft market-quality
            # miss as structurally rejected. Normalize those reasons into a
            # persistent WAIT state so one transient gate cannot destroy the
            # opportunity before another strategy or a later quote can act.
            if reasons and all(reason in _ADAPTIVE_REASONS for reason in reasons):
                return AdaptiveExecutionDecision(
                    WarriorOpportunityState.WAITING_EXECUTION,
                    reason, reasons, OpportunityConstraintKind.ADAPTIVE,
                )
            return AdaptiveExecutionDecision(
                WarriorOpportunityState.INVALIDATED,
                reason, reasons or (reason,),
                OpportunityConstraintKind.STRUCTURAL_TERMINAL,
            )

        return AdaptiveExecutionDecision(
            WarriorOpportunityState.ARMED,
            OpportunityReason.TECHNICAL_TRIGGER,
            reasons, OpportunityConstraintKind.ADAPTIVE,
        )


class WarriorOpportunityEngine:
    """Thread-safe bounded book of current Warrior execution opportunities."""

    def __init__(
        self,
        *,
        capacity: int = 128,
        policy: WarriorOpportunityPolicy | None = None,
        observer: Callable[[WarriorOpportunityTransition], None] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("opportunity capacity must be positive")
        self.capacity = capacity
        self.policy = policy or WarriorOpportunityPolicy()
        self._observer = observer
        self._items: OrderedDict[tuple[str, str], WarriorOpportunity] = OrderedDict()
        self._current_generation: dict[str, str] = {}
        self._lock = RLock()

    def arm(self, assessment: WarriorOpportunityAssessment) -> WarriorOpportunity:
        return self._transition(assessment, WarriorOpportunityState.ARMED,
                                OpportunityReason.TECHNICAL_TRIGGER)

    def apply_assessment(self, assessment: WarriorOpportunityAssessment) -> WarriorOpportunity:
        decision = self.policy.execution_decision(assessment)
        return self._transition(assessment, decision.state, decision.reason)

    def mark_authorized(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.AUTHORIZED,
                                         OpportunityReason.PAPER_ORDER_AUTHORIZED, at)

    def mark_authorization_evaluated(
        self, symbol: str, generation_id: str, *, at: datetime,
        authorized: bool, reason: str, risk_result: str | None = None,
        sizing_result: str | None = None,
    ) -> WarriorOpportunity | None:
        """Record sanitized provenance; never grant authority itself."""
        key = (symbol.strip().upper(), generation_id)
        with self._lock:
            current = self._items.get(key)
            if current is None or self._current_generation.get(key[0]) != generation_id:
                return None
            item = self._replace_locked(
                key, current,
                WarriorOpportunityState.AUTHORIZATION_EVALUATED
                if authorized else WarriorOpportunityState.REJECTED_HARD_SAFETY,
                OpportunityReason.PAPER_ORDER_AUTHORIZED
                if authorized else OpportunityReason.AUTHORIZATION_REJECTED,
                at,
            )
            item = replace(
                item,
                authorization_result="AUTHORIZED" if authorized else "REJECTED",
                authorization_reason=_sanitize_reason(reason),
                risk_result=_sanitize_optional(risk_result),
                sizing_result=_sanitize_optional(sizing_result),
            )
            self._items[key] = item
            return item

    def mark_order_intent(
        self, symbol: str, generation_id: str, *, at: datetime,
        submitted: bool = False,
    ) -> WarriorOpportunity | None:
        key = (symbol.strip().upper(), generation_id)
        with self._lock:
            current = self._items.get(key)
            if current is None or current.state not in {
                WarriorOpportunityState.AUTHORIZATION_EVALUATED,
                WarriorOpportunityState.AUTHORIZED,
            }:
                return None
            item = self._replace_locked(
                key, current, WarriorOpportunityState.ORDER_PENDING,
                OpportunityReason.ORDER_SUBMITTED if submitted
                else OpportunityReason.ORDER_INTENT_CREATED, at,
            )
            item = replace(
                item,
                order_intent_result="SUBMITTED" if submitted else "CREATED",
            )
            self._items[key] = item
            return item

    def mark_order_pending(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.ORDER_PENDING,
                                         OpportunityReason.PAPER_ORDER_PENDING, at)

    def mark_entered(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.ENTERED,
                                         OpportunityReason.PAPER_ENTRY_FILLED, at)

    def mark_partially_filled(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.PARTIALLY_FILLED,
                                         OpportunityReason.PARTIAL_ENTRY_FILLED, at)

    def mark_protected(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.PROTECTED,
                                         OpportunityReason.PROTECTION_ACTIVE, at)

    def mark_managing(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.MANAGING,
                                         OpportunityReason.POSITION_MANAGING, at)

    def mark_exit_requested(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.EXIT_REQUESTED,
                                         OpportunityReason.EXIT_REQUESTED, at)

    def expire(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.EXPIRED,
                                         OpportunityReason.OPPORTUNITY_EXPIRED, at)

    def complete(self, symbol: str, generation_id: str, *, at: datetime) -> WarriorOpportunity | None:
        return self._transition_existing(symbol, generation_id, WarriorOpportunityState.COMPLETED,
                                         OpportunityReason.LIFECYCLE_COMPLETED, at)

    def invalidate(self, symbol: str, generation_id: str, *, at: datetime,
                   reason: OpportunityReason = OpportunityReason.SETUP_INVALIDATED) -> WarriorOpportunity | None:
        key = (symbol.strip().upper(), generation_id)
        with self._lock:
            current = self._items.get(key)
            if current is None:
                return None
            return self._replace_locked(key, current, WarriorOpportunityState.INVALIDATED, reason, at)

    def quote_matches(self, symbol: str, generation_id: str) -> bool:
        with self._lock:
            current = self._items.get((symbol.strip().upper(), generation_id))
            return current is not None and current.state not in _TERMINAL_STATES

    def get(self, symbol: str, generation_id: str | None = None) -> WarriorOpportunity | None:
        normalized = symbol.strip().upper()
        with self._lock:
            selected = generation_id or self._current_generation.get(normalized)
            return None if selected is None else self._items.get((normalized, selected))

    def ranked(self, *, executable_only: bool = False) -> tuple[WarriorOpportunity, ...]:
        with self._lock:
            values = tuple(
                value for value in self._items.values()
                if value.state not in _TERMINAL_STATES
                and (not executable_only or value.state is WarriorOpportunityState.EXECUTABLE)
            )
        return tuple(sorted(values, key=self.policy.rank_key, reverse=True))

    def snapshot(self) -> tuple[WarriorOpportunity, ...]:
        with self._lock:
            return tuple(self._items.values())

    def _transition(self, assessment: WarriorOpportunityAssessment,
                    state: WarriorOpportunityState,
                    reason: OpportunityReason) -> WarriorOpportunity:
        symbol = assessment.symbol.strip().upper()
        key = (symbol, assessment.generation_id)
        with self._lock:
            prior_generation = self._current_generation.get(symbol)
            if prior_generation is not None and prior_generation != assessment.generation_id:
                prior_key = (symbol, prior_generation)
                prior = self._items.get(prior_key)
                if prior is not None and prior.state not in _TERMINAL_STATES and prior.state is not WarriorOpportunityState.ENTERED:
                    self._replace_locked(prior_key, prior, WarriorOpportunityState.INVALIDATED,
                                         OpportunityReason.SETUP_SUPERSEDED,
                                         assessment.decision_timestamp)
            current = self._items.get(key)
            if current is None:
                item = WarriorOpportunity(assessment, state, assessment.decision_timestamp,
                                          assessment.decision_timestamp, reason)
                self._items[key] = item
            else:
                item = self._replace_locked(key, current, state, reason,
                                            assessment.decision_timestamp,
                                            assessment=assessment)
            self._current_generation[symbol] = assessment.generation_id
            self._items.move_to_end(key)
            self._evict_locked()
        if current is None:
            self._emit(item)
        return item

    def _transition_existing(self, symbol: str, generation_id: str, state: WarriorOpportunityState,
                             reason: OpportunityReason, at: datetime) -> WarriorOpportunity | None:
        with self._lock:
            key = (symbol.strip().upper(), generation_id)
            if key not in self._items:
                return None
            return self._replace_locked(key, self._items[key], state, reason, at)

    def _replace_locked(self, key: tuple[str, str], current: WarriorOpportunity,
                        state: WarriorOpportunityState, reason: OpportunityReason,
                        at: datetime, *, assessment: WarriorOpportunityAssessment | None = None) -> WarriorOpportunity:
        if current.state in _TERMINAL_STATES and state not in _TERMINAL_STATES:
            return current
        if (
            current.state in {
                WarriorOpportunityState.AUTHORIZATION_EVALUATED,
                WarriorOpportunityState.AUTHORIZED,
                WarriorOpportunityState.ORDER_PENDING,
                WarriorOpportunityState.ENTERED,
                WarriorOpportunityState.PARTIALLY_FILLED,
                WarriorOpportunityState.PROTECTED,
                WarriorOpportunityState.MANAGING,
                WarriorOpportunityState.EXIT_REQUESTED,
            }
            and state in {
                WarriorOpportunityState.ARMED,
                WarriorOpportunityState.WAITING_EXECUTION,
                WarriorOpportunityState.EXECUTABLE,
            }
        ):
            return current
        changed = current.state is not state or current.reason is not reason
        item = replace(
            current,
            assessment=assessment or current.assessment,
            state=state,
            updated_at=max(at, current.updated_at),
            reason=reason,
            revision=current.revision + (1 if changed else 0),
        )
        self._items[key] = item
        self._items.move_to_end(key)
        if changed:
            self._emit(item)
        return item

    def _evict_locked(self) -> None:
        while len(self._items) > self.capacity:
            key, _ = self._items.popitem(last=False)
            symbol, generation = key
            if self._current_generation.get(symbol) == generation:
                self._current_generation.pop(symbol, None)

    def _emit(self, item: WarriorOpportunity) -> None:
        if self._observer is None:
            return
        try:
            self._observer(WarriorOpportunityTransition(
                item.assessment.symbol,
                item.assessment.generation_id,
                item.assessment.lifecycle_id,
                item.state,
                item.updated_at,
                item.reason,
            ))
        except Exception:
            # Opportunity observability must never alter entry behavior.
            return


def _sanitize_reason(value: str) -> str:
    normalized = str(value or "UNSPECIFIED").strip().upper()
    if (
        0 < len(normalized) <= 64
        and all(character.isalnum() or character == "_" for character in normalized)
    ):
        return normalized
    return "OTHER"


def _sanitize_optional(value: str | None) -> str | None:
    return None if value is None else _sanitize_reason(value)


__all__ = [
    "AdaptiveExecutionDecision", "AdaptiveOpportunityResult",
    "OpportunityConstraintKind", "OpportunityReason", "WarriorOpportunity",
    "WarriorOpportunityAssessment", "WarriorOpportunityEngine",
    "WarriorOpportunityPolicy", "WarriorOpportunityState",
    "WarriorOpportunityTransition",
]

# Strategy-neutral names for the shared lifecycle contract.  The historical
# Warrior names remain public compatibility aliases.
OpportunityAssessment = WarriorOpportunityAssessment
OpportunityLifecycle = WarriorOpportunity
OpportunityLifecycleState = WarriorOpportunityState
OpportunityEngine = WarriorOpportunityEngine
__all__ += [
    "OpportunityAssessment", "OpportunityEngine", "OpportunityLifecycle",
    "OpportunityLifecycleState",
]
