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


class ShadowClockDomain(StrEnum):
    PROVIDER_SOURCE_TIME = "PROVIDER_SOURCE_TIME"
    CALLBACK_RECEIPT_TIME = "CALLBACK_RECEIPT_TIME"
    PROCESSING_TIME = "PROCESSING_TIME"
    DECISION_SOURCE_TIME = "DECISION_SOURCE_TIME"
    DECISION_PROCESSING_TIME = "DECISION_PROCESSING_TIME"
    PERSISTENCE_TIME = "PERSISTENCE_TIME"
    LOCAL_RUNTIME_TIME = "LOCAL_RUNTIME_TIME"


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


@dataclass(frozen=True, slots=True)
class _TimingPoint:
    source_at: datetime | None
    processing_at: datetime


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
    entry_terminal: bool = False
    terminal_reason: str | None = None
    first_fresh: _TimingPoint | None = None
    first_spread_clear: _TimingPoint | None = None
    first_market_eligible: _TimingPoint | None = None
    authorization: _TimingPoint | None = None
    first_marketable: _TimingPoint | None = None
    first_marketable_spread: Decimal | None = None
    first_marketable_fresh: bool | None = None
    stop_touched: _TimingPoint | None = None
    policy_a_fill: _TimingPoint | None = None
    policy_b_fill: _TimingPoint | None = None
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
                "persistence_time": None,
                "timestamp_domains": {
                    "record_timestamp": ShadowClockDomain.DECISION_SOURCE_TIME.value,
                    "event_time": ShadowClockDomain.DECISION_SOURCE_TIME.value,
                    "processing_time": ShadowClockDomain.DECISION_PROCESSING_TIME.value,
                    "transition_written_at": ShadowClockDomain.DECISION_PROCESSING_TIME.value,
                    "persistence_time": ShadowClockDomain.PERSISTENCE_TIME.value,
                },
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
                market.symbol, _market_event_time(market) or market.observed_at,
                ShadowLatchedTransition.SESSION_INVALIDATION,
                reason="SESSION_NOT_ALLOWED",
                market=market,
                processing_time=market.observed_at,
                timestamp_domain=(
                    ShadowClockDomain.PROVIDER_SOURCE_TIME
                    if _market_event_time(market) is not None
                    else ShadowClockDomain.PROCESSING_TIME
                ),
            )
        if not market.execution_permitted:
            return self.invalidate(
                market.symbol, _market_event_time(market) or market.observed_at,
                ShadowLatchedTransition.PLAN_EXPIRED,
                reason="EXECUTION_DISABLED",
                market=market,
                processing_time=market.observed_at,
                timestamp_domain=(
                    ShadowClockDomain.PROVIDER_SOURCE_TIME
                    if _market_event_time(market) is not None
                    else ShadowClockDomain.PROCESSING_TIME
                ),
            )
        if market.halted:
            return self.invalidate(
                market.symbol, _market_event_time(market) or market.observed_at,
                ShadowLatchedTransition.HALT_INVALIDATION,
                reason="MARKET_HALTED",
                market=market,
                processing_time=market.observed_at,
                timestamp_domain=(
                    ShadowClockDomain.PROVIDER_SOURCE_TIME
                    if _market_event_time(market) is not None
                    else ShadowClockDomain.PROCESSING_TIME
                ),
            )

        version = _market_version(market, self.capture_config)
        if version == state.prior_version:
            return ()
        state.prior_version = version
        records: list[CaptureRecord] = []
        timing = _market_timing(market)
        spread = _spread_percent(market.bid, market.ask)
        spread_clear = (
            spread is not None
            and spread <= self.config.entry.maximum_spread_percent
        )
        quote_fresh = _market_fresh(market, self.capture_config)
        raw_eligible = (
            spread_clear and quote_fresh and market.tradable
            and not market.halted and market.execution_permitted
        )
        marketable = market.ask is not None and market.ask <= state.plan.trigger

        if (
            market.last <= state.plan.stop
            and state.stop_touched is None
            and not state.policy_b_possible
        ):
            state.stop_touched = timing
            state.entry_terminal = True
            state.terminal_reason = ShadowLatchedTransition.STOP_TOUCHED_BEFORE_ENTRY.value
            records.extend(self._transition(
                state, ShadowLatchedTransition.STOP_TOUCHED_BEFORE_ENTRY,
                market,
                extra={
                    "hard_invalidation": True,
                    "entry_terminal": True,
                    "price_source": "LAST",
                },
            ))

        eligible = raw_eligible and not state.entry_terminal

        if spread_clear and state.first_spread_clear is None:
            state.first_spread_clear = timing
        if quote_fresh and state.first_fresh is None:
            state.first_fresh = timing

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
                if state.first_market_eligible is None:
                    state.first_market_eligible = timing
                if (
                    state.authorization is None
                    and not state.entry_terminal
                    and bool(state.account_payload.get("risk_approved"))
                    and not bool(state.account_payload.get("existing_strategy_position"))
                ):
                    state.authorization = timing
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
        if marketable and state.first_marketable is None:
            state.first_marketable = timing
            state.first_marketable_spread = spread
            state.first_marketable_fresh = quote_fresh
        state.halted = market.halted

        if (
            state.authorization is not None and marketable
            and not state.entry_terminal
            and not state.policy_a_possible
        ):
            state.policy_a_possible = True
            state.policy_a_fill = timing
            records.extend(self._transition(
                state, ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE,
                market,
                extra={
                    "policy": "A_AUTHORIZATION_SPREAD_ONLY",
                    "policy_freshness_semantics": "AUTHORIZATION_ONLY_RESEARCH_NOT_PRODUCTION_FRESHNESS",
                    "fill_observation_market_eligible": raw_eligible,
                    "liquidity_evidence_available": False,
                },
            ))
        if (
            state.authorization is not None and marketable and eligible
            and not state.entry_terminal
            and not state.policy_b_possible
        ):
            state.policy_b_possible = True
            state.policy_b_fill = timing
            records.extend(self._transition(
                state, ShadowLatchedTransition.HYPOTHETICAL_FILL_POSSIBLE,
                market,
                extra={
                    "policy": "B_SPREAD_THROUGH_FILL",
                    "policy_freshness_semantics": "AUTHORIZATION_AND_FILL_MARKET_ELIGIBILITY_REQUIRED",
                    "fill_observation_market_eligible": True,
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
        timestamp_domain: ShadowClockDomain = ShadowClockDomain.PROVIDER_SOURCE_TIME,
    ) -> tuple[CaptureRecord, ...]:
        state = self._active.pop(symbol.strip().upper(), None)
        if state is None:
            return ()
        state.entry_terminal = True
        state.terminal_reason = reason
        observed = market or state.plan.original_market
        records = list(self._transition(
            state, transition, observed,
            extra={"reason": reason, "hard_invalidation": True},
            timestamp=timestamp,
            processing_time=processing_time,
            timestamp_domain=timestamp_domain,
        ))
        if transition is not ShadowLatchedTransition.PLAN_EXPIRED:
            records.extend(self._transition(
                state, ShadowLatchedTransition.PLAN_EXPIRED, observed,
                extra={"reason": reason}, timestamp=timestamp,
                processing_time=processing_time,
                timestamp_domain=timestamp_domain,
            ))
        source_time = (
            timestamp
            if timestamp_domain is ShadowClockDomain.PROVIDER_SOURCE_TIME
            else None
        )
        records.append(_outcome_record(
            state, source_time, reason,
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
                timestamp_domain=ShadowClockDomain.PROCESSING_TIME,
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
        timestamp_domain: ShadowClockDomain = ShadowClockDomain.PROVIDER_SOURCE_TIME,
    ) -> tuple[CaptureRecord, ...]:
        processed_at = processing_time or market.observed_at
        source_at = (
            timestamp
            if timestamp is not None
            and timestamp_domain is ShadowClockDomain.PROVIDER_SOURCE_TIME
            else None if timestamp is not None else _market_event_time(market)
        )
        at = source_at or processed_at
        market_blockers = _market_blockers(
            market, self.config, self.capture_config,
            terminal_reason=(state.terminal_reason if state.entry_terminal else None),
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
            "event_time": source_at,
            "processing_time": processed_at,
            "cause_event_time": source_at,
            "transition_written_at": processed_at,
            "persistence_time": None,
            "entry_terminal": state.entry_terminal,
            "terminal_reason": state.terminal_reason,
            "timestamp_domains": {
                "record_timestamp": (
                    ShadowClockDomain.PROVIDER_SOURCE_TIME.value
                    if source_at is not None
                    else ShadowClockDomain.PROCESSING_TIME.value
                ),
                "event_time": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
                "processing_time": ShadowClockDomain.PROCESSING_TIME.value,
                "cause_event_time": (
                    timestamp_domain.value if timestamp is not None else
                    ShadowClockDomain.PROVIDER_SOURCE_TIME.value
                ),
                "transition_written_at": ShadowClockDomain.PROCESSING_TIME.value,
                "persistence_time": ShadowClockDomain.PERSISTENCE_TIME.value,
            },
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
    outcomes = {
        str(record.payload.get("plan_id")): record
        for record in records
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_OUTCOME
    }
    for plan_id in plans:
        outcome = outcomes.get(str(plan_id))
        if outcome is None:
            continue
        duration = outcome.payload.get("source_time_to_market_eligible_seconds")
        if duration is not None:
            clear_times.append(Decimal(str(duration)))
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
            "authorization_observable": False,
            "execution_authority": False,
            "risk_evaluator": "size_position",
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
        "allowed_symbol": signal.symbol in account.allowed_symbols,
        "risk_engine_approved": account.risk_engine_approved,
        "broker_restriction": account.broker_restriction,
        "existing_strategy_position": existing_strategy_position,
        "authoritative_order_state_available": False,
        "mutated_account_state": False,
        "captured_at": captured_at,
        "evaluation_semantics": "READ_ONLY_PLAN_CREATION_SNAPSHOT",
        "requires_execution_time_reassessment": True,
        "authorization_observable": True,
        "execution_authority": False,
        "risk_evaluator": "size_position",
    }


def _market_payload(
    market: ShadowMarketObservation,
    capture_config: ForwardCaptureConfiguration,
) -> dict[str, object]:
    last_age, quote_age = _market_ages(market)
    source_at = _market_event_time(market)
    last_age_domain, quote_age_domain = _market_age_domains(market)
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
        "last_age_clock_domain": last_age_domain,
        "quote_age_clock_domain": quote_age_domain,
        "age_semantics": "CALLBACK_RECEIPT_TO_PROCESSING_ELSE_PROVIDER_SOURCE_TO_SOURCE",
        "source_event_time": source_at,
        "timestamp_domains": {
            "observed_at": ShadowClockDomain.PROCESSING_TIME.value,
            "last_timestamp": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
            "quote_timestamp": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
            "last_received_timestamp": ShadowClockDomain.CALLBACK_RECEIPT_TIME.value,
            "quote_received_timestamp": ShadowClockDomain.CALLBACK_RECEIPT_TIME.value,
            "source_event_time": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
        },
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


def _elapsed_seconds(
    start: datetime | None, end: datetime | None,
) -> Decimal | None:
    if start is None or end is None or end < start:
        return None
    return Decimal(str((end - start).total_seconds()))


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
    source_at = _market_event_time(market)
    return (
        _elapsed_seconds(market.last_received_timestamp, market.observed_at)
        if market.last_received_timestamp is not None
        else _elapsed_seconds(market.last_timestamp, source_at),
        _elapsed_seconds(market.quote_received_timestamp, market.observed_at)
        if market.quote_received_timestamp is not None
        else _elapsed_seconds(market.quote_timestamp, source_at),
    )


def _market_age_domains(
    market: ShadowMarketObservation,
) -> tuple[str | None, str | None]:
    return (
        ShadowClockDomain.LOCAL_RUNTIME_TIME.value
        if market.last_received_timestamp is not None
        else ShadowClockDomain.PROVIDER_SOURCE_TIME.value
        if market.last_timestamp is not None and _market_event_time(market) is not None
        else None,
        ShadowClockDomain.LOCAL_RUNTIME_TIME.value
        if market.quote_received_timestamp is not None
        else ShadowClockDomain.PROVIDER_SOURCE_TIME.value
        if market.quote_timestamp is not None and _market_event_time(market) is not None
        else None,
    )


def _market_blockers(
    market: ShadowMarketObservation,
    config: WarriorMomentumConfig,
    capture_config: ForwardCaptureConfiguration,
    *,
    terminal_reason: str | None = None,
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
    if terminal_reason is not None:
        blockers.append(terminal_reason)
    return tuple(blockers)


def _outcome_record(
    state: _LatchedState, source_time: datetime | None, reason: str,
    *, processing_time: datetime,
) -> CaptureRecord:
    decision_timing = _TimingPoint(
        state.plan.created_at, state.plan.original_market.observed_at,
    )
    invalidation_timing = _TimingPoint(source_time, processing_time)
    durations: dict[str, object] = {}
    for name, timing in (
        ("fresh", state.first_fresh),
        ("spread_clear", state.first_spread_clear),
        ("market_eligible", state.first_market_eligible),
        ("marketable", state.first_marketable),
        ("authorization", state.authorization),
        ("policy_a_fill", state.policy_a_fill),
        ("policy_b_fill", state.policy_b_fill),
        ("stop", state.stop_touched),
        ("invalidation", invalidation_timing),
    ):
        durations.update(_duration_payload(name, decision_timing, timing))
    record_timestamp = source_time or processing_time
    return CaptureRecord.create(
        CaptureRecordType.SHADOW_LATCHED_OUTCOME,
        state.plan.symbol,
        record_timestamp,
        {
            "plan_id": state.plan.plan_id,
            "decision_record_id": state.plan.decision_record_id,
            "authority": "SHADOW_ONLY_NON_EXECUTABLE",
            "reason": reason,
            "first_clear_at": _processing_at(state.first_market_eligible),
            "first_clear_source_at": _source_at(state.first_market_eligible),
            "first_clear_processing_at": _processing_at(state.first_market_eligible),
            "time_to_clear_seconds": durations["source_time_to_market_eligible_seconds"],
            "time_to_clear_clock_domain": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
            "clear_cycles": state.clear_cycles,
            "reblock_cycles": state.reblock_cycles,
            "authorization_at": _processing_at(state.authorization),
            "authorization_source_at": _source_at(state.authorization),
            "authorization_processing_at": _processing_at(state.authorization),
            "first_marketable_at": _processing_at(state.first_marketable),
            "first_marketable_source_at": _source_at(state.first_marketable),
            "first_marketable_processing_at": _processing_at(state.first_marketable),
            "first_marketable_spread_percent": state.first_marketable_spread,
            "first_marketable_quote_fresh": state.first_marketable_fresh,
            "plan_invalidated_before_marketability": state.first_marketable is None,
            "stop_touched_before_entry": state.stop_touched is not None,
            "stop_touched_at": _processing_at(state.stop_touched),
            "stop_touched_source_at": _source_at(state.stop_touched),
            "stop_touched_processing_at": _processing_at(state.stop_touched),
            "stop_touched_before_marketability": (
                state.stop_touched is not None
                and (
                    state.first_marketable is None
                    or state.stop_touched.processing_at
                    <= state.first_marketable.processing_at
                )
            ),
            "entry_terminal": state.entry_terminal,
            "terminal_reason": state.terminal_reason,
            "hypothetical_order_possible": state.authorization is not None,
            "policy_a_fill_possible": state.policy_a_possible,
            "policy_b_fill_possible": state.policy_b_possible,
            "policy_result_differs": state.policy_a_possible != state.policy_b_possible,
            "hypothetical_fill_claimed": False,
            "paper_submission_attempted": False,
            "rest_confirmation_requested": False,
            **durations,
            "duration_semantics_version": 2,
            "event_time": source_time,
            "processing_time": processing_time,
            "cause_event_time": source_time,
            "transition_written_at": processing_time,
            "persistence_time": None,
            "timestamp_domains": {
                "record_timestamp": (
                    ShadowClockDomain.PROVIDER_SOURCE_TIME.value
                    if source_time is not None
                    else ShadowClockDomain.PROCESSING_TIME.value
                ),
                "event_time": ShadowClockDomain.PROVIDER_SOURCE_TIME.value,
                "processing_time": ShadowClockDomain.PROCESSING_TIME.value,
                "transition_written_at": ShadowClockDomain.PROCESSING_TIME.value,
                "persistence_time": ShadowClockDomain.PERSISTENCE_TIME.value,
            },
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


def _market_timing(market: ShadowMarketObservation) -> _TimingPoint:
    return _TimingPoint(_market_event_time(market), market.observed_at)


def _source_at(timing: _TimingPoint | None) -> datetime | None:
    return None if timing is None else timing.source_at


def _processing_at(timing: _TimingPoint | None) -> datetime | None:
    return None if timing is None else timing.processing_at


def _duration_payload(
    name: str, start: _TimingPoint, end: _TimingPoint | None,
) -> dict[str, Decimal | None]:
    return {
        f"source_time_to_{name}_seconds": (
            None if end is None
            else _elapsed_seconds(start.source_at, end.source_at)
        ),
        f"processing_time_to_{name}_seconds": (
            None if end is None
            else _elapsed_seconds(start.processing_at, end.processing_at)
        ),
    }


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
    "ShadowClockDomain", "ShadowLatchedPlan", "ShadowLatchedPlanResearch",
    "ShadowLatchedResearchSummary", "ShadowLatchedTransition",
    "ShadowMarketObservation", "analyze_shadow_latched",
    "analyze_shadow_latched_store",
]
