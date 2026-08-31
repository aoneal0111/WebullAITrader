"""Non-executable fast-market research for completed-bar technical plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from hashlib import sha256
from statistics import median
from typing import TYPE_CHECKING

from .configuration import WarriorMomentumConfig
from .forward_models import (
    CaptureRecord,
    CaptureRecordType,
    ForwardCaptureConfiguration,
    PaperAccountContext,
    PointInTimeObservation,
)
from .models import (
    MomentumCandidate,
    MomentumEntrySignal,
    ReasonCode,
    SetupState,
)
from .risk import size_position

if TYPE_CHECKING:
    from .forward_store import ForwardCaptureStore

HUNDRED = Decimal("100")
_EXECUTION_VARIABLE_REJECTIONS = frozenset({
    ReasonCode.SPREAD_WIDE,
    ReasonCode.STALE_MARKET_DATA,
})
_REPORTED_VARIABLE_BLOCKERS = frozenset({
    *_EXECUTION_VARIABLE_REJECTIONS,
    ReasonCode.AWAITING_EXECUTION_QUOTE,
})


class ShadowLatchedTransition(StrEnum):
    PLAN_CREATED = "PLAN_CREATED"
    MARKET_BLOCKED = "MARKET_BLOCKED"
    MARKET_ELIGIBLE = "MARKET_ELIGIBLE"
    MARKET_REBLOCKED = "MARKET_REBLOCKED"
    QUOTE_STALE = "QUOTE_STALE"
    QUOTE_FRESH = "QUOTE_FRESH"
    SPREAD_BLOCKED = "SPREAD_BLOCKED"
    SPREAD_CLEAR = "SPREAD_CLEAR"
    LIMIT_MARKETABLE = "LIMIT_MARKETABLE"
    LIMIT_NOT_MARKETABLE = "LIMIT_NOT_MARKETABLE"
    STOP_TOUCHED_BEFORE_ENTRY = "STOP_TOUCHED_BEFORE_ENTRY"
    NEW_BAR_INVALIDATION = "NEW_BAR_INVALIDATION"
    SESSION_INVALIDATION = "SESSION_INVALIDATION"
    HALT_INVALIDATION = "HALT_INVALIDATION"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PLAN_REPLACED = "PLAN_REPLACED"
    ACCOUNT_CONTEXT_UNAVAILABLE = "ACCOUNT_CONTEXT_UNAVAILABLE"
    HYPOTHETICAL_ORDER_AUTHORIZED = "HYPOTHETICAL_ORDER_AUTHORIZED"
    HYPOTHETICAL_FILL_POSSIBLE = "HYPOTHETICAL_FILL_POSSIBLE"


@dataclass(frozen=True, slots=True)
class ShadowMarketObservation:
    symbol: str
    observed_at: datetime
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    last_timestamp: datetime | None
    quote_timestamp: datetime | None
    last_received_timestamp: datetime | None
    quote_received_timestamp: datetime | None
    halted: bool
    tradable: bool
    session: str
    execution_permitted: bool
    last_age_seconds: Decimal | None = None
    quote_age_seconds: Decimal | None = None
    freshness_authoritative: bool = False

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("shadow observation timestamp must be timezone-aware")
        for name in (
            "last_timestamp", "quote_timestamp", "last_received_timestamp",
            "quote_received_timestamp",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")

    @classmethod
    def from_point(
        cls, value: PointInTimeObservation, *, execution_permitted: bool,
    ) -> "ShadowMarketObservation":
        observation = value.observation
        return cls(
            observation.symbol,
            value.evaluation_timestamp or observation.timestamp,
            observation.price,
            observation.bid,
            observation.ask,
            value.last_price_observed_at or observation.last_price_timestamp,
            value.quote_observed_at or observation.quote_timestamp,
            observation.last_price_received_timestamp,
            observation.quote_received_timestamp,
            observation.halted,
            observation.tradable,
            value.session,
            execution_permitted,
            value.last_price_freshness_seconds,
            value.quote_freshness_seconds,
            True,
        )


@dataclass(frozen=True, slots=True)
class ShadowLatchedPlan:
    plan_id: str
    symbol: str
    created_at: datetime
    decision_record_id: str
    technical_bar_version: tuple[datetime, ...]
    setup_type: str
    setup_state: str
    setup_score: Decimal
    trigger: Decimal
    stop: Decimal
    reference_price: Decimal
    technical_evidence: tuple[tuple[str, str], ...]
    original_blockers: tuple[str, ...]
    original_market: ShadowMarketObservation


@dataclass(slots=True)
class _LatchedState:
    plan: ShadowLatchedPlan
    account_payload: dict[str, object]
    prior_version: tuple[object, ...] | None = None
    spread_clear: bool | None = None
    quote_fresh: bool | None = None
    market_eligible: bool | None = None
    limit_marketable: bool | None = None
    halted: bool = False
    stop_touched: bool = False
    authorization_at: datetime | None = None
    first_marketable_at: datetime | None = None
    first_marketable_spread: Decimal | None = None
    first_marketable_fresh: bool | None = None
    first_clear_at: datetime | None = None
    stop_touched_at: datetime | None = None
    clear_cycles: int = 0
    reblock_cycles: int = 0
    policy_a_possible: bool = False
    policy_b_possible: bool = False


class ShadowLatchedPlanResearch:
    """Observe hypothetical eligibility without access to execution ports."""

    def __init__(
        self,
        config: WarriorMomentumConfig = WarriorMomentumConfig(),
        capture_config: ForwardCaptureConfiguration = ForwardCaptureConfiguration(),
    ) -> None:
        self.config = config
        self.capture_config = capture_config
        self._active: dict[str, _LatchedState] = {}
        self._running = True

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def create(
        self,
        candidate: MomentumCandidate,
        technical_signal: MomentumEntrySignal | None,
        value: PointInTimeObservation,
        *,
        decision_record_id: str,
        entry_rejections: tuple[ReasonCode, ...],
        account: PaperAccountContext | None,
        existing_strategy_position: bool,
        execution_permitted: bool,
    ) -> tuple[CaptureRecord, ...]:
        if not self._running or technical_signal is None:
            return ()
        setup = candidate.setup
        rejection_set = frozenset(entry_rejections)
        if (
            setup is None
            or setup.state is not SetupState.TRIGGERED
            or setup.trigger is None
            or setup.stop_price is None
            or not rejection_set
            or not rejection_set <= _EXECUTION_VARIABLE_REJECTIONS
        ):
            return ()

        records: list[CaptureRecord] = []
        if candidate.symbol in self._active:
            records.extend(self.invalidate(
                candidate.symbol, candidate.timestamp,
                ShadowLatchedTransition.PLAN_REPLACED,
                reason="AUTHORITATIVE_TECHNICAL_REPLACEMENT",
                processing_time=(
                    value.evaluation_timestamp or candidate.timestamp
                ),
            ))

        bar_version = tuple(bar.timestamp for bar in value.bars)
        identity = "|".join((
            candidate.symbol,
            candidate.timestamp.isoformat(),
            setup.setup_type.value,
            str(setup.trigger),
            str(setup.stop_price),
            *(item.isoformat() for item in bar_version),
        ))
        plan_id = sha256(identity.encode("utf-8")).hexdigest()
        market = ShadowMarketObservation.from_point(
            value, execution_permitted=execution_permitted,
        )
        plan = ShadowLatchedPlan(
            plan_id,
            candidate.symbol,
            candidate.timestamp,
            decision_record_id,
            bar_version,
            setup.setup_type.value,
            setup.state.value,
            setup.score,
            setup.trigger,
            setup.stop_price,
            candidate.price,
            (
                ("momentum_score", str(candidate.score.total)),
                ("dollar_volume", str(candidate.dollar_volume)),
                ("relative_volume", str(candidate.relative_volume)),
                ("float_shares", "UNKNOWN" if candidate.float_shares is None else str(candidate.float_shares)),
                ("policy_version", candidate.policy_version),
            ),
            tuple(dict.fromkeys(
                code.value
                for code in (*entry_rejections, *candidate.reason_codes)
                if code in _REPORTED_VARIABLE_BLOCKERS
            )),
            market,
        )
        account_payload = _account_research(
            technical_signal, account,
            existing_strategy_position=existing_strategy_position,
            config=self.config,
            captured_at=candidate.timestamp,
        )
        state = _LatchedState(plan, account_payload)
        self._active[candidate.symbol] = state
        records.append(CaptureRecord.create(
            CaptureRecordType.SHADOW_LATCHED_PLAN,
            candidate.symbol,
            candidate.timestamp,
            {
                "plan_id": plan_id,
                "decision_record_id": decision_record_id,
                "authority": "SHADOW_ONLY_NON_EXECUTABLE",
                "technical_bar_version": bar_version,
                "setup_type": plan.setup_type,
                "setup_state": plan.setup_state,
                "setup_score": plan.setup_score,
                "trigger": plan.trigger,
                "stop": plan.stop,
                "reference_price": plan.reference_price,
                "technical_evidence": dict(plan.technical_evidence),
                "original_blockers": plan.original_blockers,
                "original_market": _market_payload(market, self.capture_config),
                "spread_threshold_percent": self.config.entry.maximum_spread_percent,
                "freshness_threshold_seconds": self.capture_config.quote_stale_after_seconds,
                "account_research": account_payload,
                "production_signal_mutated": False,
                "paper_submission_attempted": False,
                "rest_confirmation_requested": False,
                "event_time": candidate.timestamp,
                "processing_time": market.observed_at,
                "transition_written_at": market.observed_at,
            },
            identity_parts=(plan_id,),
        ))
        records.extend(self._transition(
            state, ShadowLatchedTransition.PLAN_CREATED, market,
        ))
        if not account_payload["account_available"]:
            records.extend(self._transition(
                state, ShadowLatchedTransition.ACCOUNT_CONTEXT_UNAVAILABLE,
                market,
            ))
        records.extend(self.observe(market))
        return tuple(records)

    def observe(
        self, market: ShadowMarketObservation,
    ) -> tuple[CaptureRecord, ...]:
        if not self._running:
            return ()
        state = self._active.get(market.symbol.strip().upper())
        if state is None:
            return ()
        if market.session not in self.config.entry.allowed_sessions:
            return self.invalidate(
                market.symbol, market.observed_at,
                ShadowLatchedTransition.SESSION_INVALIDATION,
                reason="SESSION_NOT_ALLOWED",
                market=market,
            )
        if not market.execution_permitted:
            return self.invalidate(
                market.symbol, market.observed_at,
                ShadowLatchedTransition.PLAN_EXPIRED,
                reason="EXECUTION_DISABLED",
                market=market,
            )

        version = _market_version(market, self.capture_config)
        if version == state.prior_version:
            return ()
        state.prior_version = version
        records: list[CaptureRecord] = []
        spread = _spread_percent(market.bid, market.ask)
        spread_clear = (
            spread is not None
            and spread <= self.config.entry.maximum_spread_percent
        )
        quote_fresh = _market_fresh(market, self.capture_config)
        eligible = (
            spread_clear and quote_fresh and market.tradable
            and not market.halted and market.execution_permitted
        )
        marketable = market.ask is not None and market.ask <= state.plan.trigger

        records.extend(self._changed_transition(
            state, "spread_clear", spread_clear,
            ShadowLatchedTransition.SPREAD_CLEAR,
            ShadowLatchedTransition.SPREAD_BLOCKED,
            market,
        ))
        records.extend(self._changed_transition(
            state, "quote_fresh", quote_fresh,
            ShadowLatchedTransition.QUOTE_FRESH,
            ShadowLatchedTransition.QUOTE_STALE,
            market,
        ))
        prior_eligible = state.market_eligible
        if eligible != prior_eligible:
            transition = (
                ShadowLatchedTransition.MARKET_ELIGIBLE
                if eligible
                else ShadowLatchedTransition.MARKET_BLOCKED
                if prior_eligible is None
                else ShadowLatchedTransition.MARKET_REBLOCKED
            )
            records.extend(self._transition(state, transition, market))
            state.market_eligible = eligible
            if eligible:
                state.clear_cycles += 1
                if state.first_clear_at is None:
                    state.first_clear_at = market.observed_at
                if (
                    state.authorization_at is None
                    and bool(state.account_payload.get("risk_approved"))
                    and not bool(state.account_payload.get("existing_strategy_position"))
                ):
                    state.authorization_at = market.observed_at
                    records.extend(self._transition(
                        state,
                        ShadowLatchedTransition.HYPOTHETICAL_ORDER_AUTHORIZED,
                        market,
                    ))
            elif prior_eligible:
                state.reblock_cycles += 1

        records.extend(self._changed_transition(
            state, "limit_marketable", marketable,
            ShadowLatchedTransition.LIMIT_MARKETABLE,
            ShadowLatchedTransition.LIMIT_NOT_MARKETABLE,
            market,
        ))
        if marketable and state.first_marketable_at is None:
            state.first_marketable_at = market.observed_at
            state.first_marketable_spread = spread
            state.first_marketable_fresh = quote_fresh
        if market.last <= state.plan.stop and not state.stop_touched:
            state.stop_touched = True
            state.stop_touched_at = market.observed_at
            records.extend(self._transition(
                state, ShadowLatchedTransition.STOP_TOUCHED_BEFORE_ENTRY,
                market,
                extra={"hard_invalidation": False, "price_source": "LAST"},
            ))
        if market.halted and not state.halted:
            records.extend(self._transition(
                state, ShadowLatchedTransition.HALT_INVALIDATION, market,
                extra={"hard_invalidation": False},
            ))
        state.halted = market.halted

        if (
            state.authorization_at is not None and marketable
            and not state.policy_a_possible
        ):
            state.policy_a_possible = True
            records.extend(self._transition(
                state, ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE,
                market,
                extra={
                    "policy": "A_AUTHORIZATION_SPREAD_ONLY",
                    "liquidity_evidence_available": False,
                },
            ))
        if (
            state.authorization_at is not None and marketable and eligible
            and not state.policy_b_possible
        ):
            state.policy_b_possible = True
            records.extend(self._transition(
                state, ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE,
                market,
                extra={
                    "policy": "B_SPREAD_THROUGH_FILL",
                    "liquidity_evidence_available": False,
                },
            ))
        return tuple(records)

    def invalidate(
        self,
        symbol: str,
        timestamp: datetime,
        transition: ShadowLatchedTransition,
        *,
        reason: str,
        market: ShadowMarketObservation | None = None,
        processing_time: datetime | None = None,
    ) -> tuple[CaptureRecord, ...]:
        state = self._active.pop(symbol.strip().upper(), None)
        if state is None:
            return ()
        observed = market or state.plan.original_market
        records = list(self._transition(
            state, transition, observed,
            extra={"reason": reason, "hard_invalidation": True},
            timestamp=timestamp,
            processing_time=processing_time,
        ))
        if transition is not ShadowLatchedTransition.PLAN_EXPIRED:
            records.extend(self._transition(
                state, ShadowLatchedTransition.PLAN_EXPIRED, observed,
                extra={"reason": reason}, timestamp=timestamp,
                processing_time=processing_time,
            ))
        records.append(_outcome_record(
            state, timestamp, reason,
            processing_time=processing_time or observed.observed_at,
        ))
        return tuple(records)

    def shutdown(self, timestamp: datetime) -> tuple[CaptureRecord, ...]:
        if not self._running:
            return ()
        self._running = False
        records: list[CaptureRecord] = []
        for symbol in tuple(self._active):
            records.extend(self.invalidate(
                symbol, timestamp, ShadowLatchedTransition.PLAN_EXPIRED,
                reason="RUNTIME_SHUTDOWN",
                processing_time=timestamp,
            ))
        return tuple(records)

    def _changed_transition(
        self,
        state: _LatchedState,
        attribute: str,
        value: bool,
        when_true: ShadowLatchedTransition,
        when_false: ShadowLatchedTransition,
        market: ShadowMarketObservation,
    ) -> tuple[CaptureRecord, ...]:
        if getattr(state, attribute) == value:
            return ()
        setattr(state, attribute, value)
        return self._transition(
            state, when_true if value else when_false, market,
        )

    def _transition(
        self,
        state: _LatchedState,
        transition: ShadowLatchedTransition,
        market: ShadowMarketObservation,
        *,
        extra: dict[str, object] | None = None,
        timestamp: datetime | None = None,
        processing_time: datetime | None = None,
    ) -> tuple[CaptureRecord, ...]:
        at = timestamp or market.observed_at
        processed_at = processing_time or market.observed_at
        market_blockers = _market_blockers(
            market, self.config, self.capture_config,
        )
        payload: dict[str, object] = {
            "plan_id": state.plan.plan_id,
            "decision_record_id": state.plan.decision_record_id,
            "transition": transition.value,
            "authority": "SHADOW_ONLY_NON_EXECUTABLE",
            "market": _market_payload(market, self.capture_config),
            "market_blockers": market_blockers,
            "market_eligible": not market_blockers,
            "limit_marketable": (
                market.ask is not None and market.ask <= state.plan.trigger
            ),
            "structural_limit_price": state.plan.trigger,
            "structural_stop_price": state.plan.stop,
            "hypothetical_quantity": state.account_payload.get("quantity"),
            "spread_threshold_percent": self.config.entry.maximum_spread_percent,
            "freshness_threshold_seconds": self.capture_config.quote_stale_after_seconds,
            "paper_submission_attempted": False,
            "rest_confirmation_requested": False,
            "event_time": _market_event_time(market),
            "processing_time": processed_at,
            "cause_event_time": timestamp,
            "transition_written_at": processed_at,
        }
        if extra:
            payload.update(extra)
        return (CaptureRecord.create(
            CaptureRecordType.SHADOW_LATCHED_TRANSITION,
            state.plan.symbol,
            at,
            payload,
            identity_parts=(state.plan.plan_id, transition.value, at.isoformat(), str(extra)),
        ),)


@dataclass(frozen=True, slots=True)
class ShadowLatchedResearchSummary:
    total_plans: int
    initially_blocked_plans: int
    blocker_cleared_plans: int
    p50_time_to_clear_seconds: Decimal | None
    p90_time_to_clear_seconds: Decimal | None
    clear_cycles: int
    reblock_cycles: int
    limit_marketable_plans: int
    account_approved_plans: int
    policy_a_fill_possible: int
    policy_b_fill_possible: int
    hypothetical_order_possible_plans: int
    stop_before_entry: int
    new_bar_invalidations: int
    correlated_outcomes: tuple[tuple[int, int], ...]


def analyze_shadow_latched(records: tuple[CaptureRecord, ...]) -> ShadowLatchedResearchSummary:
    plans = {
        record.payload["plan_id"]: record
        for record in records
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_PLAN
    }
    transitions = [
        record for record in records
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_TRANSITION
    ]
    by_plan: dict[str, list[CaptureRecord]] = {key: [] for key in plans}
    for record in transitions:
        by_plan.setdefault(str(record.payload.get("plan_id")), []).append(record)
    clear_times: list[Decimal] = []
    plan_decision_ids = {
        str(record.payload.get("decision_record_id"))
        for record in plans.values()
    }
    horizons: dict[int, int] = {}
    for record in records:
        if (
            record.record_type is CaptureRecordType.SHADOW_OUTCOME
            and str(record.payload.get("decision_record_id")) in plan_decision_ids
            and record.payload.get("horizon_minutes") is not None
        ):
            horizon = int(record.payload["horizon_minutes"])
            horizons[horizon] = horizons.get(horizon, 0) + 1
    for plan_id, plan_record in plans.items():
        clear = next((
            item for item in by_plan.get(plan_id, ())
            if item.payload.get("transition") == ShadowLatchedTransition.MARKET_ELIGIBLE.value
        ), None)
        if clear is not None:
            clear_times.append(Decimal(str(
                (clear.timestamp - plan_record.timestamp).total_seconds()
            )))
    transition_values = [str(item.payload.get("transition")) for item in transitions]
    account_approved = sum(
        bool(record.payload.get("account_research", {}).get("risk_approved"))
        for record in plans.values()
    )
    return ShadowLatchedResearchSummary(
        len(plans),
        sum(
            ShadowLatchedTransition.MARKET_BLOCKED.value in {
                str(item.payload.get("transition"))
                for item in by_plan.get(plan_id, ())
            }
            for plan_id in plans
        ),
        len(clear_times),
        _percentile(clear_times, Decimal("0.50")),
        _percentile(clear_times, Decimal("0.90")),
        transition_values.count(ShadowLatchedTransition.MARKET_ELIGIBLE.value),
        transition_values.count(ShadowLatchedTransition.MARKET_REBLOCKED.value),
        sum(
            any(item.payload.get("transition") == ShadowLatchedTransition.LIMIT_MARKETABLE.value
                for item in by_plan.get(plan_id, ()))
            for plan_id in plans
        ),
        account_approved,
        sum(
            any(
                item.payload.get("transition") == ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE.value
                and item.payload.get("policy") == "A_AUTHORIZATION_SPREAD_ONLY"
                for item in by_plan.get(plan_id, ())
            ) for plan_id in plans
        ),
        sum(
            any(
                item.payload.get("transition") == ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE.value
                and item.payload.get("policy") == "B_SPREAD_THROUGH_FILL"
                for item in by_plan.get(plan_id, ())
            ) for plan_id in plans
        ),
        sum(
            any(
                item.payload.get("transition")
                == ShadowLatchedTransition.HYPOTHETICAL_ORDER_AUTHORIZED.value
                for item in by_plan.get(plan_id, ())
            ) for plan_id in plans
        ),
        transition_values.count(ShadowLatchedTransition.STOP_TOUCHED_BEFORE_ENTRY.value),
        transition_values.count(ShadowLatchedTransition.NEW_BAR_INVALIDATION.value),
        tuple(sorted(horizons.items())),
    )


def analyze_shadow_latched_store(
    store: "ForwardCaptureStore",
) -> ShadowLatchedResearchSummary:
    """Build a read-only report from the existing immutable capture store."""

    relevant_types = {
        CaptureRecordType.SHADOW_LATCHED_PLAN,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        CaptureRecordType.SHADOW_LATCHED_OUTCOME,
        CaptureRecordType.SHADOW_OUTCOME,
    }
    return analyze_shadow_latched(tuple(
        record for record in store.records()
        if record.record_type in relevant_types
    ))


def _account_research(
    signal: MomentumEntrySignal,
    account: PaperAccountContext | None,
    *,
    existing_strategy_position: bool,
    config: WarriorMomentumConfig,
    captured_at: datetime,
) -> dict[str, object]:
    if account is None:
        return {
            "account_available": False,
            "risk_approved": False,
            "quantity": None,
            "risk_dollars": None,
            "buying_power": None,
            "exposure_result": "ACCOUNT_CONTEXT_UNAVAILABLE",
            "existing_strategy_position": existing_strategy_position,
            "authoritative_order_state_available": False,
            "captured_at": captured_at,
            "evaluation_semantics": "ACCOUNT_CONTEXT_UNAVAILABLE",
            "requires_execution_time_reassessment": True,
        }
    result = size_position(
        signal,
        account_equity=account.equity,
        buying_power=account.buying_power,
        allowed_symbols=account.allowed_symbols,
        existing_exposure=account.existing_exposure,
        exposure_limit=account.exposure_limit,
        risk_engine_approved=account.risk_engine_approved,
        broker_restriction=account.broker_restriction,
        config=config.risk,
    )
    return {
        "account_available": True,
        "risk_approved": result.approved,
        "quantity": result.shares,
        "risk_dollars": result.risk_dollars,
        "buying_power": account.buying_power,
        "existing_exposure": account.existing_exposure,
        "exposure_limit": account.exposure_limit,
        "exposure_result": "PASSED" if result.approved else "REJECTED",
        "reason_codes": tuple(code.value for code in result.reason_codes),
        "existing_strategy_position": existing_strategy_position,
        "authoritative_order_state_available": False,
        "mutated_account_state": False,
        "captured_at": captured_at,
        "evaluation_semantics": "READ_ONLY_PLAN_CREATION_SNAPSHOT",
        "requires_execution_time_reassessment": True,
    }


def _market_payload(
    market: ShadowMarketObservation,
    capture_config: ForwardCaptureConfiguration,
) -> dict[str, object]:
    last_age, quote_age = _market_ages(market)
    fresh = _market_fresh(market, capture_config)
    return {
        "observed_at": market.observed_at,
        "last": market.last,
        "bid": market.bid,
        "ask": market.ask,
        "last_timestamp": market.last_timestamp,
        "quote_timestamp": market.quote_timestamp,
        "last_received_timestamp": market.last_received_timestamp,
        "quote_received_timestamp": market.quote_received_timestamp,
        "last_age_seconds": last_age,
        "quote_age_seconds": quote_age,
        "spread_percent": _spread_percent(market.bid, market.ask),
        "halted": market.halted,
        "tradable": market.tradable,
        "session": market.session,
        "execution_permitted": market.execution_permitted,
        "confirmation_would_be_required": not fresh,
        "confirmation_reason": None if fresh else "STREAMING_EXECUTION_DATA_STALE",
    }


def _market_version(
    market: ShadowMarketObservation,
    capture_config: ForwardCaptureConfiguration,
) -> tuple[object, ...]:
    return (
        market.last_timestamp,
        market.quote_timestamp,
        market.last,
        market.bid,
        market.ask,
        market.halted,
        market.tradable,
        market.session,
        market.execution_permitted,
        market.last_received_timestamp,
        market.quote_received_timestamp,
        market.last_age_seconds,
        market.quote_age_seconds,
        market.freshness_authoritative,
        _market_fresh(market, capture_config),
    )


def _spread_percent(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None or ask < bid:
        return None
    midpoint = (bid + ask) / Decimal("2")
    return None if midpoint <= 0 else (ask - bid) / midpoint * HUNDRED


def _age(observed_at: datetime, provider_at: datetime | None) -> Decimal | None:
    if provider_at is None:
        return None
    return Decimal(str(max(0, (observed_at - provider_at).total_seconds())))


def _market_fresh(
    market: ShadowMarketObservation,
    capture_config: ForwardCaptureConfiguration,
) -> bool:
    last_age, quote_age = _market_ages(market)
    return (
        last_age is not None
        and quote_age is not None
        and last_age <= capture_config.quote_stale_after_seconds
        and quote_age <= capture_config.quote_stale_after_seconds
    )


def _market_ages(
    market: ShadowMarketObservation,
) -> tuple[Decimal | None, Decimal | None]:
    if market.freshness_authoritative:
        return market.last_age_seconds, market.quote_age_seconds
    return (
        _age(market.observed_at, market.last_timestamp),
        _age(market.observed_at, market.quote_timestamp),
    )


def _market_blockers(
    market: ShadowMarketObservation,
    config: WarriorMomentumConfig,
    capture_config: ForwardCaptureConfiguration,
) -> tuple[str, ...]:
    blockers: list[str] = []
    spread = _spread_percent(market.bid, market.ask)
    if spread is None or spread > config.entry.maximum_spread_percent:
        blockers.append(ReasonCode.SPREAD_WIDE.value)
    if not _market_fresh(market, capture_config):
        blockers.append(ReasonCode.STALE_MARKET_DATA.value)
    if market.halted:
        blockers.append(ReasonCode.HALTED.value)
    if not market.tradable:
        blockers.append(ReasonCode.NOT_TRADABLE.value)
    if market.session not in config.entry.allowed_sessions:
        blockers.append(ReasonCode.SESSION_NOT_ALLOWED.value)
    if not market.execution_permitted:
        blockers.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
    return tuple(blockers)


def _outcome_record(
    state: _LatchedState, timestamp: datetime, reason: str,
    *, processing_time: datetime,
) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.SHADOW_LATCHED_OUTCOME,
        state.plan.symbol,
        timestamp,
        {
            "plan_id": state.plan.plan_id,
            "decision_record_id": state.plan.decision_record_id,
            "authority": "SHADOW_ONLY_NON_EXECUTABLE",
            "reason": reason,
            "first_clear_at": state.first_clear_at,
            "time_to_clear_seconds": (
                None if state.first_clear_at is None
                else Decimal(str((state.first_clear_at - state.plan.created_at).total_seconds()))
            ),
            "clear_cycles": state.clear_cycles,
            "reblock_cycles": state.reblock_cycles,
            "authorization_at": state.authorization_at,
            "first_marketable_at": state.first_marketable_at,
            "first_marketable_spread_percent": state.first_marketable_spread,
            "first_marketable_quote_fresh": state.first_marketable_fresh,
            "plan_invalidated_before_marketability": state.first_marketable_at is None,
            "stop_touched_before_entry": state.stop_touched,
            "stop_touched_at": state.stop_touched_at,
            "stop_touched_before_marketability": (
                state.stop_touched_at is not None
                and (
                    state.first_marketable_at is None
                    or state.stop_touched_at <= state.first_marketable_at
                )
            ),
            "hypothetical_order_possible": state.authorization_at is not None,
            "policy_a_fill_possible": state.policy_a_possible,
            "policy_b_fill_possible": state.policy_b_possible,
            "policy_result_differs": state.policy_a_possible != state.policy_b_possible,
            "hypothetical_fill_claimed": False,
            "paper_submission_attempted": False,
            "rest_confirmation_requested": False,
            "event_time": timestamp,
            "processing_time": processing_time,
            "cause_event_time": timestamp,
            "transition_written_at": processing_time,
        },
        identity_parts=(state.plan.plan_id, "OUTCOME", reason),
    )


def _market_event_time(market: ShadowMarketObservation) -> datetime | None:
    values = tuple(
        value
        for value in (market.last_timestamp, market.quote_timestamp)
        if value is not None
    )
    return max(values) if values else None


def _percentile(
    values: list[Decimal], percentile: Decimal,
) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if percentile == Decimal("0.50"):
        return Decimal(str(median(ordered)))
    index = max(0, int((Decimal(len(ordered)) * percentile).to_integral_value(rounding=ROUND_CEILING)) - 1)
    return ordered[index]


__all__ = [
    "ShadowLatchedPlan", "ShadowLatchedPlanResearch",
    "ShadowLatchedResearchSummary", "ShadowLatchedTransition",
    "ShadowMarketObservation", "analyze_shadow_latched",
    "analyze_shadow_latched_store",
]
