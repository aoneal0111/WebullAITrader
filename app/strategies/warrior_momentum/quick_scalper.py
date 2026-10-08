"""PAPER-only quick-scalp opportunity policy on Atlas's shared boundaries.

This module owns no broker or market-data client.  It converts one immutable
point-in-time executable snapshot into an opportunity assessment and delegates
authorization/submission to injected canonical services.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Callable

from .opportunity_engine import (
    AdaptiveOpportunityResult, OpportunityReason,
    WarriorOpportunityAssessment, WarriorOpportunityEngine,
    WarriorOpportunityState,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class StrategyOwner(StrEnum):
    WARRIOR_MOMENTUM = "WARRIOR_MOMENTUM"
    QUICK_SCALPER = "QUICK_SCALPER"


class ScalpDecision(StrEnum):
    WAIT = "WAIT"
    EXECUTABLE = "EXECUTABLE"
    REJECTED_HARD_SAFETY = "REJECTED_HARD_SAFETY"


@dataclass(frozen=True, slots=True)
class QuickScalperConfig:
    enabled: bool = False
    minimum_price: Decimal = Decimal('1.00')
    maximum_price: Decimal | None = None
    allowed_sessions: frozenset[str] = frozenset(
        {"PREMARKET", "REGULAR", "AFTER_HOURS"}
    )
    provider_freshness_seconds: Decimal = Decimal("5")
    absolute_minimum_move: Decimal = Decimal("0.05")
    volatility_move_fraction: Decimal = Decimal("0.50")
    execution_cost_multiple: Decimal = Decimal("1.75")
    slippage_per_side: Decimal = Decimal("0.01")
    maximum_holding_seconds: int = 300
    cooldown_seconds: int = 120
    attempt_window_seconds: int = 3600
    maximum_attempts_per_symbol: int = 3
    maximum_concurrent_positions: int = 2
    maximum_consecutive_losses: int = 3
    maximum_top_of_book_participation_fraction: Decimal = Decimal('0.25')
    full_liquidity_top_of_book_quantity: Decimal = Decimal('100')
    strategy_loss_guard: Decimal = Decimal("300")

    def __post_init__(self) -> None:
        if self.minimum_price != Decimal('1.00'):
            raise ValueError('Quick Scalper minimum price must remain $1.00')
        if self.maximum_price is not None:
            raise ValueError('Quick Scalper must not impose a maximum price')
        if self.provider_freshness_seconds != Decimal("5"):
            raise ValueError("Quick Scalper must preserve the 5-second provider limit")
        if not (
            ZERO < self.maximum_top_of_book_participation_fraction <= 1
        ):
            raise ValueError('top-of-book participation must be in (0, 1]')
        if self.full_liquidity_top_of_book_quantity <= ZERO:
            raise ValueError('full-liquidity quote quantity must be positive')
        if min(
            self.absolute_minimum_move, self.volatility_move_fraction,
            self.execution_cost_multiple, self.slippage_per_side,
            self.strategy_loss_guard,
        ) < ZERO:
            raise ValueError("Quick Scalper bounds must be non-negative")
        if min(
            self.maximum_holding_seconds, self.cooldown_seconds,
            self.attempt_window_seconds, self.maximum_attempts_per_symbol,
            self.maximum_concurrent_positions, self.maximum_consecutive_losses,
        ) <= 0:
            raise ValueError("Quick Scalper activity bounds must be positive")


@dataclass(frozen=True, slots=True)
class QuickScalpSnapshot:
    symbol: str
    generation_id: str
    setup_type: str
    observed_at: datetime
    decision_at: datetime
    last: Decimal
    bid: Decimal
    ask: Decimal
    last_timestamp: datetime
    bid_timestamp: datetime
    ask_timestamp: datetime
    structural_stop: Decimal
    short_horizon_range: Decimal
    velocity_cents_per_minute: Decimal
    velocity_percent_per_minute: Decimal
    relative_volume: Decimal
    dollar_liquidity: Decimal
    setup_quality: Decimal
    momentum_priority: Decimal
    catalyst_context: str
    session: str
    relative_volume_status: str = 'AVAILABLE'
    executable_ask_size: Decimal | None = None
    stream_sample_count: int | None = None
    stream_elapsed_seconds: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.generation_id.strip():
            raise ValueError("scalp identity is required")
        for value in (
            self.observed_at, self.decision_at, self.last_timestamp,
            self.bid_timestamp, self.ask_timestamp,
        ):
            if value.tzinfo is None:
                raise ValueError("scalp timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class QuickScalpAssessment:
    opportunity: WarriorOpportunityAssessment
    decision: ScalpDecision
    target_move: Decimal
    target_price: Decimal
    round_trip_cost: Decimal
    expected_move: Decimal
    reason: str
    required_bid_move: Decimal = ZERO


class QuickScalperPolicy:
    def __init__(self, config: QuickScalperConfig = QuickScalperConfig()) -> None:
        self.config = config

    def assess(self, value: QuickScalpSnapshot) -> QuickScalpAssessment:
        hard = self._hard_reason(value)
        spread = value.ask - value.bid
        liquidity_slippage = ZERO
        if value.executable_ask_size is not None:
            shortfall = max(
                ZERO,
                self.config.full_liquidity_top_of_book_quantity
                - value.executable_ask_size,
            ) / self.config.full_liquidity_top_of_book_quantity
            liquidity_slippage = self.config.slippage_per_side * min(
                Decimal('1'), shortfall,
            )
        cost = (
            max(ZERO, spread)
            + (self.config.slippage_per_side + liquidity_slippage) * 2
        )
        target_move = max(
            self.config.absolute_minimum_move,
            value.short_horizon_range * self.config.volatility_move_fraction,
            cost * self.config.execution_cost_multiple,
        )
        expected_move = max(
            ZERO, value.short_horizon_range,
            value.velocity_cents_per_minute,
            value.bid * value.velocity_percent_per_minute / HUNDRED,
        )
        # Movement is measured from BID; the sell target is anchored to ASK.
        # Include the distance between those anchors and estimated exit slippage.
        required_bid_move = (
            max(ZERO, value.ask + target_move - value.bid)
            + self.config.slippage_per_side + liquidity_slippage
        )
        adaptive = AdaptiveOpportunityResult.EXECUTABLE
        reasons: tuple[OpportunityReason, ...] = ()
        decision = ScalpDecision.EXECUTABLE
        reason = "POSITIVE_EXECUTABLE_EDGE"
        if hard is not None:
            adaptive = AdaptiveOpportunityResult.WAIT
            decision = ScalpDecision.REJECTED_HARD_SAFETY
            reason = hard.value
            reasons = (hard,)
        elif expected_move < required_bid_move:
            adaptive = AdaptiveOpportunityResult.WAIT
            decision = ScalpDecision.WAIT
            reason = "INSUFFICIENT_NET_EXECUTABLE_EDGE"
            reasons = (OpportunityReason.EXECUTION_QUALITY_WAIT,)
        risk = value.ask - value.structural_stop
        spread_percent = spread / value.ask * HUNDRED if value.ask > ZERO else None
        opportunity = WarriorOpportunityAssessment(
            symbol=value.symbol,
            setup_family=value.setup_type,
            generation_id=value.generation_id,
            lifecycle_id=f"QUICK_SCALPER|{value.symbol.upper()}|{value.generation_id}",
            decision_timestamp=value.decision_at,
            scanner_timestamp=value.observed_at,
            quote_timestamp=min(value.bid_timestamp, value.ask_timestamp),
            last_timestamp=value.last_timestamp,
            structural_trigger=value.ask,
            executable_entry=value.ask,
            structural_stop=value.structural_stop,
            risk_per_share=risk if risk > ZERO else Decimal("0.00000001"),
            target_levels=(value.ask + target_move,),
            remaining_first_target_r=(target_move - cost) / risk if risk > ZERO else None,
            remaining_final_target_r=(target_move - cost) / risk if risk > ZERO else None,
            spread_percent=spread_percent,
            execution_cost=cost,
            execution_quality="GOOD" if decision is ScalpDecision.EXECUTABLE else "POOR",
            dollar_liquidity=value.dollar_liquidity,
            relative_volume=value.relative_volume,
            volatility_context=value.short_horizon_range,
            catalyst_context=value.catalyst_context,
            setup_quality=value.setup_quality,
            momentum_priority=value.momentum_priority,
            displacement_percent=ZERO,
            adaptive_result=adaptive,
            hard_safety_reason=hard,
            reason_codes=reasons,
            strategy=StrategyOwner.QUICK_SCALPER.value,
            executable_bid=value.bid,
            provider_last_timestamp=value.last_timestamp,
            provider_bid_timestamp=value.bid_timestamp,
            provider_ask_timestamp=value.ask_timestamp,
            execution_quote_timestamp=value.decision_at,
        )
        return QuickScalpAssessment(
            opportunity, decision, target_move, value.ask + target_move,
            cost, expected_move, reason, required_bid_move,
        )

    def _hard_reason(self, value: QuickScalpSnapshot) -> OpportunityReason | None:
        if value.session not in self.config.allowed_sessions:
            return OpportunityReason.SESSION_NOT_ALLOWED
        if value.ask < self.config.minimum_price:
            return OpportunityReason.PRICE_NOT_ELIGIBLE
        if min(value.last, value.bid, value.ask) <= ZERO or value.bid >= value.ask:
            return OpportunityReason.INVALID_EXECUTION_QUOTE
        if value.structural_stop <= ZERO or value.structural_stop >= value.ask:
            return OpportunityReason.RISK_NOT_AUTHORIZED
        # Execution safety is anchored to the executable market, not to
        # whether a new trade happened to print recently.  A retained LAST can
        # legitimately be older in thin or extended-hours trading while a
        # current BID/ASK still represents the market we can actually enter
        # and exit against.  Future provider timestamps remain fail-closed for
        # every component because they indicate a clock/provenance defect.
        last_age = Decimal(str(
            (value.decision_at - value.last_timestamp).total_seconds()
        ))
        if last_age < ZERO:
            return OpportunityReason.PROVIDER_DATA_STALE
        for timestamp in (value.bid_timestamp, value.ask_timestamp):
            age = Decimal(str((value.decision_at - timestamp).total_seconds()))
            if age < ZERO or age > self.config.provider_freshness_seconds:
                return OpportunityReason.PROVIDER_DATA_STALE
        return None


class SymbolOwnershipRegistry:
    """Mutually exclusive symbol ownership when strategy lots are unavailable."""

    def __init__(self) -> None:
        self._owners: dict[str, tuple[StrategyOwner, str]] = {}
        self._lock = RLock()

    def acquire(self, symbol: str, owner: StrategyOwner, lifecycle_id: str) -> bool:
        key = symbol.strip().upper()
        with self._lock:
            current = self._owners.get(key)
            if current is not None and current != (owner, lifecycle_id):
                return False
            self._owners[key] = (owner, lifecycle_id)
            return True

    def release(self, symbol: str, owner: StrategyOwner, lifecycle_id: str) -> bool:
        key = symbol.strip().upper()
        with self._lock:
            if self._owners.get(key) != (owner, lifecycle_id):
                return False
            self._owners.pop(key, None)
            return True

    def owner(self, symbol: str) -> tuple[StrategyOwner, str] | None:
        with self._lock:
            return self._owners.get(symbol.strip().upper())


class QuickScalperGuard:
    def __init__(self, config: QuickScalperConfig) -> None:
        self.config = config
        self._attempts: dict[str, list[datetime]] = {}
        self._last_closed: dict[str, datetime] = {}
        self._generations: set[tuple[str, str]] = set()
        self._open: set[str] = set()
        self._consecutive_losses = 0
        self._loss = ZERO

    def allow(self, symbol: str, generation: str, at: datetime) -> tuple[bool, str]:
        key = symbol.strip().upper()
        if (key, generation) in self._generations:
            return False, "DUPLICATE_GENERATION"
        if len(self._open) >= self.config.maximum_concurrent_positions:
            return False, "CONCURRENT_POSITION_LIMIT"
        if self._consecutive_losses >= self.config.maximum_consecutive_losses:
            return False, "CONSECUTIVE_LOSS_GUARD"
        if self._loss >= self.config.strategy_loss_guard:
            return False, "STRATEGY_LOSS_GUARD"
        closed = self._last_closed.get(key)
        if closed is not None and at - closed < timedelta(seconds=self.config.cooldown_seconds):
            return False, "SYMBOL_COOLDOWN"
        cutoff = at - timedelta(seconds=self.config.attempt_window_seconds)
        recent = [stamp for stamp in self._attempts.get(key, ()) if stamp >= cutoff]
        self._attempts[key] = recent
        if len(recent) >= self.config.maximum_attempts_per_symbol:
            return False, "ATTEMPT_LIMIT"
        return True, "ALLOWED"

    def record_attempt(self, symbol: str, generation: str, at: datetime) -> None:
        key = symbol.strip().upper()
        self._generations.add((key, generation))
        self._attempts.setdefault(key, []).append(at)
        self._open.add(key)

    def record_close(self, symbol: str, at: datetime, pnl: Decimal) -> None:
        key = symbol.strip().upper()
        self._open.discard(key)
        self._last_closed[key] = at
        if pnl < ZERO:
            self._loss += -pnl
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0


@dataclass(frozen=True, slots=True)
class CanonicalScalpSubmission:
    authorized: bool
    intent_created: bool
    submitted: bool
    protection_confirmed: bool
    reason: str
    lifecycle_id: str | None = None
    order_id: str | None = None
    recoverable: bool = False


class QuickScalper:
    """Coordinates policy with injected canonical authorization/order service."""

    def __init__(
        self, *, engine: WarriorOpportunityEngine,
        ownership: SymbolOwnershipRegistry, config: QuickScalperConfig,
        authorize_and_submit: Callable[
            [QuickScalpAssessment], CanonicalScalpSubmission
        ],
    ) -> None:
        self.engine = engine
        self.ownership = ownership
        self.config = config
        self.policy = QuickScalperPolicy(config)
        self.guard = QuickScalperGuard(config)
        self._submit = authorize_and_submit

    def observe(self, snapshot: QuickScalpSnapshot) -> QuickScalpAssessment:
        assessed = self.policy.assess(snapshot)
        self.engine.arm(assessed.opportunity)
        item = self.engine.apply_assessment(assessed.opportunity)
        if item.state in {
            WarriorOpportunityState.AUTHORIZATION_EVALUATED,
            WarriorOpportunityState.AUTHORIZED,
            WarriorOpportunityState.ORDER_PENDING,
            WarriorOpportunityState.ENTERED,
        }:
            return assessed
        if not self.config.enabled:
            return assessed
        stale_preview_refresh = (
            assessed.decision is ScalpDecision.REJECTED_HARD_SAFETY
            and assessed.reason == OpportunityReason.PROVIDER_DATA_STALE.value
            and not any(
                timestamp > snapshot.decision_at
                for timestamp in (
                    snapshot.last_timestamp,
                    snapshot.bid_timestamp,
                    snapshot.ask_timestamp,
                )
            )
        )
        if (
            assessed.decision is not ScalpDecision.EXECUTABLE
            and not stale_preview_refresh
        ):
            return assessed
        allowed, reason = self.guard.allow(
            snapshot.symbol, snapshot.generation_id, snapshot.decision_at,
        )
        if not allowed:
            self.engine.mark_authorization_evaluated(
                snapshot.symbol, snapshot.generation_id,
                at=snapshot.decision_at, authorized=False, reason=reason,
            )
            return assessed
        if not self.ownership.acquire(
            snapshot.symbol, StrategyOwner.QUICK_SCALPER,
            item.assessment.lifecycle_id,
        ):
            self.engine.mark_authorization_evaluated(
                snapshot.symbol, snapshot.generation_id,
                at=snapshot.decision_at, authorized=False,
                reason="SYMBOL_OWNED_BY_OTHER_STRATEGY",
            )
            return assessed
        try:
            result = self._submit(assessed)
        except Exception:
            result = CanonicalScalpSubmission(
                False, False, False, False, "CANONICAL_SUBMISSION_FAILURE"
            )
        recoverable_confirmation = result.reason in {
            'EXECUTION_QUOTE_UNAVAILABLE',
            'INSUFFICIENT_NET_EXECUTABLE_EDGE',
            'PROVIDER_DATA_STALE',
            'INVALID_EXECUTION_QUOTE',
            'INSUFFICIENT_EXECUTABLE_SIZE',
        }
        if (
            (result.recoverable or recoverable_confirmation)
            and not result.authorized
        ):
            # Quote availability and adaptive executable economics can improve
            # on the next streaming event. Preserve the exact generation in a
            # WAIT state instead of turning a bounded confirmation miss into
            # terminal hard rejection.
            self.engine.apply_assessment(replace(
                assessed.opportunity,
                adaptive_result=AdaptiveOpportunityResult.WAIT,
                hard_safety_reason=None,
                reason_codes=(OpportunityReason.EXECUTION_QUALITY_WAIT,),
            ))
        else:
            self.engine.mark_authorization_evaluated(
                snapshot.symbol, snapshot.generation_id,
                at=snapshot.decision_at, authorized=result.authorized,
                reason=result.reason,
            )
        if result.authorized and result.intent_created:
            self.engine.mark_authorized(
                snapshot.symbol, snapshot.generation_id, at=snapshot.decision_at,
            )
            self.engine.mark_order_intent(
                snapshot.symbol, snapshot.generation_id,
                at=snapshot.decision_at, submitted=result.submitted,
            )
        # Entry submission is the durable activity boundary. Protection is
        # required immediately after a non-zero fill, not before an unfilled
        # LIMIT entry exists.
        if result.submitted:
            self.guard.record_attempt(
                snapshot.symbol, snapshot.generation_id, snapshot.decision_at,
            )
        elif not result.submitted:
            self.ownership.release(
                snapshot.symbol, StrategyOwner.QUICK_SCALPER,
                item.assessment.lifecycle_id,
            )
        return assessed


__all__ = [
    "CanonicalScalpSubmission", "QuickScalpAssessment", "QuickScalpSnapshot",
    "QuickScalper", "QuickScalperConfig", "QuickScalperGuard",
    "QuickScalperPolicy", "ScalpDecision", "StrategyOwner",
    "SymbolOwnershipRegistry",
]
