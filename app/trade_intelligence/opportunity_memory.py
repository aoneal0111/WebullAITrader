"""Bounded, observation-only memory for one trading opportunity.

This module deliberately does not authorize orders.  It records the compact
continuity state that is otherwise lost when a current scanner observation has
no actionable setup.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from queue import Full, Queue
from threading import Event, Lock, Thread
from typing import Callable


ZERO = Decimal("0")


class OpportunityMemoryState(StrEnum):
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    SETUP_DEVELOPING = "SETUP_DEVELOPING"
    ACTIONABLE = "ACTIONABLE"
    POSITIONED = "POSITIONED"
    COOLING = "COOLING"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


class OpportunityTransitionType(StrEnum):
    DISCOVERED = "DISCOVERED"
    WATCHING = "WATCHING"
    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    SETUP_FORMING = "SETUP_FORMING"
    SETUP_READY = "SETUP_READY"
    SETUP_TEMPORARILY_ABSENT = "SETUP_TEMPORARILY_ABSENT"
    OPPORTUNITY_INVALIDATED = "OPPORTUNITY_INVALIDATED"
    ENTRY_ATTEMPT = "ENTRY_ATTEMPT"
    ENTRY_REPLACED = "ENTRY_REPLACED"
    ENTRY_EXPIRED = "ENTRY_EXPIRED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    ENTRY_REARMED = "ENTRY_REARMED"
    PULLBACK_STARTED = "PULLBACK_STARTED"
    PULLBACK_UPDATED = "PULLBACK_UPDATED"
    PULLBACK_CONFIRMED = "PULLBACK_CONFIRMED"
    RECLAIM_STARTED = "RECLAIM_STARTED"
    RECLAIM_CONFIRMED = "RECLAIM_CONFIRMED"
    NEW_HOD = "NEW_HOD"
    SPREAD_BLOCKED = "SPREAD_BLOCKED"
    SPREAD_RECOVERED = "SPREAD_RECOVERED"
    LIQUIDITY_BLOCKED = "LIQUIDITY_BLOCKED"
    LIQUIDITY_RECOVERED = "LIQUIDITY_RECOVERED"
    CATALYST_ADDED = "CATALYST_ADDED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_ADDED = "POSITION_ADDED"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    POSITION_CLOSED = "POSITION_CLOSED"
    STRUCTURE_ESTABLISHED = "STRUCTURE_ESTABLISHED"
    LIVE_TRIGGER_FIRST_SEEN = "LIVE_TRIGGER_FIRST_SEEN"
    NEW_STRUCTURE_ENTRY_READY = "NEW_STRUCTURE_ENTRY_READY"


@dataclass(frozen=True, slots=True)
class OpportunityTransition:
    kind: OpportunityTransitionType
    occurred_at: datetime
    opportunity_id: str
    symbol: str
    trading_date: date
    reason: str | None = None
    setup: str | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EntryAttemptSummary:
    lifecycle_id: str | None
    setup: str | None
    attempted_at: datetime
    requested_price: Decimal | None
    requested_quantity: Decimal | None
    structural_stop: Decimal | None
    spread: Decimal | None
    liquidity: Decimal | None
    risk: Decimal | None
    result: str
    replacement_count: int = 0
    reason: str | None = None
    actual_filled_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class NewStructureAssessment:
    eligible: bool
    structure: str
    reason: str | None = None
    entry_anchor: Decimal | None = None
    structural_stop: Decimal | None = None
    risk_per_share: Decimal | None = None
    trigger_price: Decimal | None = None


class PullbackClassification(StrEnum):
    """Classification supplied by the completed-bar structural detector."""

    HEALTHY = "PULLBACK_HEALTHY"
    DEEP_BUT_VALID = "PULLBACK_DEEP_BUT_VALID"
    FAILED = "PULLBACK_FAILED"
    UNAVAILABLE = "PULLBACK_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ContinuationStructureAssessment:
    """A completed-structure assessment before the live trigger."""

    eligible: bool
    structure: str
    classification: PullbackClassification
    reason: str | None = None
    entry_anchor: Decimal | None = None
    structural_stop: Decimal | None = None
    reclaim_level: Decimal | None = None
    risk_per_share: Decimal | None = None


@dataclass(slots=True)
class OpportunityMemoryRecord:
    opportunity_id: str
    symbol: str
    trading_date: date
    first_seen_at: datetime
    first_seen_price: Decimal | None = None
    first_qualified_at: datetime | None = None
    first_qualified_price: Decimal | None = None
    original_setup: str | None = None
    current_setup: str | None = None
    current_setup_state: str | None = None
    original_entry_anchor: Decimal | None = None
    latest_entry_attempt_price: Decimal | None = None
    entry_attempt_count: int = 0
    latest_entry_result: str | None = None
    highest_price_since_start: Decimal | None = None
    highest_price_at: datetime | None = None
    post_peak_pullback_low: Decimal | None = None
    pullback_low_at: datetime | None = None
    pullback_depth_percent: Decimal | None = None
    latest_reclaim_level: Decimal | None = None
    latest_reclaim_at: datetime | None = None
    hod: Decimal | None = None
    hod_at: datetime | None = None
    vwap_relation: Decimal | None = None
    momentum_score: Decimal | None = None
    momentum_state: str | None = None
    spread: Decimal | None = None
    dollar_volume: Decimal | None = None
    relative_volume: Decimal | None = None
    catalyst_summary: str | None = None
    catalyst_timestamp: datetime | None = None
    lifecycle_count: int = 0
    authoritative_position_quantity: Decimal = ZERO
    actual_filled_quantity: Decimal = ZERO
    first_taken: bool = False
    second_taken: bool = False
    runner_state: str | None = None
    latest_blocking_reason: str | None = None
    opportunity_state: OpportunityMemoryState = OpportunityMemoryState.DISCOVERED
    last_meaningful_change_at: datetime | None = None
    structure_established_at: datetime | None = None
    live_trigger_first_seen_at: datetime | None = None
    live_trigger_anchor: Decimal | None = None
    entry_authorized_at: datetime | None = None
    order_submitted_at: datetime | None = None
    attempts: deque[EntryAttemptSummary] = field(default_factory=lambda: deque(maxlen=8))
    transitions: deque[OpportunityTransition] = field(default_factory=lambda: deque(maxlen=64))


_BLOCKING_REASONS = {
    "SPREAD_WIDE", "LIQUIDITY_LOW", "RVOL_LOW", "NO_SETUP", "STALE_MARKET_DATA",
    "RISK_REJECTED", "NOT_TRADABLE", "HALTED", "SESSION_NOT_ALLOWED",
}


class OpportunityMemory:
    """Bounded hot opportunity memory; all methods are observation-only."""

    def __init__(self, *, max_active: int = 500, transition_capacity: int = 64) -> None:
        if max_active <= 0 or transition_capacity <= 0:
            raise ValueError("opportunity memory bounds must be positive")
        self.max_active = max_active
        self.transition_capacity = transition_capacity
        self._records: OrderedDict[tuple[date, str, str], OpportunityMemoryRecord] = OrderedDict()
        self._last_signature: dict[tuple[date, str, str], tuple[object, ...]] = {}
        self._lock = Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def get(self, trading_date: date, symbol: str, opportunity_id: str) -> OpportunityMemoryRecord | None:
        key = self._key(trading_date, symbol, opportunity_id)
        with self._lock:
            return self._records.get(key)

    def records(self) -> tuple[OpportunityMemoryRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def observe(
        self,
        *,
        opportunity_id: str,
        symbol: str,
        trading_date: date,
        observed_at: datetime,
        price: Decimal | None = None,
        qualified: bool = False,
        setup: str | None = None,
        setup_state: str | None = None,
        entry_anchor: Decimal | None = None,
        momentum_score: Decimal | None = None,
        spread: Decimal | None = None,
        dollar_volume: Decimal | None = None,
        relative_volume: Decimal | None = None,
        hod: Decimal | None = None,
        vwap_relation: Decimal | None = None,
        blocking_reason: str | None = None,
        opportunity_state: OpportunityMemoryState | None = None,
        transition: OpportunityTransitionType | None = None,
        transition_reason: str | None = None,
    ) -> OpportunityMemoryRecord:
        key = self._key(trading_date, symbol, opportunity_id)
        emitted: OpportunityTransition | None = None
        with self._lock:
            record = self._records.get(key)
            if record is None:
                record = OpportunityMemoryRecord(
                    opportunity_id=str(opportunity_id), symbol=symbol.strip().upper(),
                    trading_date=trading_date, first_seen_at=observed_at,
                    first_seen_price=price,
                    transitions=deque(maxlen=self.transition_capacity),
                )
                self._records[key] = record
                self._emit_locked(record, OpportunityTransitionType.DISCOVERED, observed_at, price=price)
            self._records.move_to_end(key)
            if record.first_qualified_at is None and qualified:
                record.first_qualified_at, record.first_qualified_price = observed_at, price
                self._emit_locked(record, OpportunityTransitionType.QUALIFIED, observed_at, price=price)
            if record.original_setup is None and setup:
                record.original_setup = setup
            if record.original_entry_anchor is None and entry_anchor is not None:
                record.original_entry_anchor = entry_anchor
            record.current_setup = setup
            record.current_setup_state = setup_state
            record.momentum_score = momentum_score
            record.spread = spread
            record.dollar_volume = dollar_volume
            record.relative_volume = relative_volume
            record.vwap_relation = vwap_relation
            if hod is not None and (record.hod is None or hod > record.hod):
                record.hod, record.hod_at = hod, observed_at
                self._emit_locked(record, OpportunityTransitionType.NEW_HOD, observed_at, price=hod)
            if price is not None and (record.highest_price_since_start is None or price > record.highest_price_since_start):
                record.highest_price_since_start, record.highest_price_at = price, observed_at
            if record.highest_price_since_start is not None and price is not None and price < record.highest_price_since_start:
                if record.post_peak_pullback_low is None or price < record.post_peak_pullback_low:
                    record.post_peak_pullback_low, record.pullback_low_at = price, observed_at
                    record.pullback_depth_percent = (
                        (record.highest_price_since_start - price) / record.highest_price_since_start * Decimal("100")
                    )
                    self._emit_locked(record, OpportunityTransitionType.PULLBACK_UPDATED, observed_at, price=price)
            if blocking_reason is not None:
                record.latest_blocking_reason = blocking_reason
            if opportunity_state is not None:
                record.opportunity_state = opportunity_state
            elif qualified:
                record.opportunity_state = (
                    OpportunityMemoryState.ACTIONABLE
                    if setup_state == "TRIGGERED"
                    else OpportunityMemoryState.SETUP_DEVELOPING
                    if setup_state == "FORMING"
                    else OpportunityMemoryState.ACTIVE
                )
            elif record.opportunity_state not in {OpportunityMemoryState.INVALIDATED, OpportunityMemoryState.CLOSED}:
                record.opportunity_state = OpportunityMemoryState.ACTIVE
            signature = (setup, setup_state, qualified, blocking_reason, record.opportunity_state)
            if signature != self._last_signature.get(key):
                self._last_signature[key] = signature
                if transition is not None:
                    emitted = self._emit_locked(record, transition, observed_at, price=price, reason=transition_reason)
                elif setup_state in {"FORMING", "TRIGGERED"}:
                    emitted = self._emit_locked(
                        record,
                        OpportunityTransitionType.SETUP_READY if setup_state == "TRIGGERED" else OpportunityTransitionType.SETUP_FORMING,
                        observed_at, price=price, setup=setup,
                    )
                elif not setup and record.current_setup_state is None:
                    emitted = self._emit_locked(record, OpportunityTransitionType.SETUP_TEMPORARILY_ABSENT, observed_at, price=price)
                elif blocking_reason in _BLOCKING_REASONS:
                    emitted = self._emit_locked(record, OpportunityTransitionType.SPREAD_BLOCKED if blocking_reason == "SPREAD_WIDE" else OpportunityTransitionType.LIQUIDITY_BLOCKED, observed_at, reason=blocking_reason)
            if emitted is not None:
                record.last_meaningful_change_at = observed_at
            while len(self._records) > self.max_active:
                old_key, old_record = self._records.popitem(last=False)
                self._last_signature.pop(old_key, None)
            return record

    def invalidate(self, trading_date: date, symbol: str, opportunity_id: str, at: datetime, reason: str) -> None:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            return
        with self._lock:
            record.opportunity_state = OpportunityMemoryState.INVALIDATED
            record.latest_blocking_reason = reason
            self._emit_locked(record, OpportunityTransitionType.OPPORTUNITY_INVALIDATED, at, reason=reason)
            record.last_meaningful_change_at = at

    def record_transition(
        self, trading_date: date, symbol: str, opportunity_id: str,
        at: datetime, kind: OpportunityTransitionType, *, reason: str | None = None,
        price: Decimal | None = None, quantity: Decimal | None = None,
    ) -> OpportunityTransition | None:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            raise KeyError("opportunity must be observed before recording a transition")
        with self._lock:
            event = self._emit_locked(record, kind, at, price=price, quantity=quantity, reason=reason)
            if event is not None:
                record.last_meaningful_change_at = at
            return event

    def record_entry_attempt(self, trading_date: date, symbol: str, opportunity_id: str,
                             attempt: EntryAttemptSummary, *,
                             structure_established_at: datetime | None = None,
                             live_trigger_first_seen_at: datetime | None = None,
                             entry_authorized_at: datetime | None = None,
                             order_submitted_at: datetime | None = None) -> None:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            raise KeyError("opportunity must be observed before recording an entry attempt")
        with self._lock:
            record.attempts.append(attempt)
            record.entry_attempt_count += 1
            record.latest_entry_attempt_price = attempt.requested_price
            record.latest_entry_result = attempt.result
            record.latest_blocking_reason = attempt.reason
            if structure_established_at is not None:
                record.structure_established_at = structure_established_at
            if live_trigger_first_seen_at is not None:
                record.live_trigger_first_seen_at = live_trigger_first_seen_at
            if entry_authorized_at is not None:
                record.entry_authorized_at = entry_authorized_at
            if order_submitted_at is not None:
                record.order_submitted_at = order_submitted_at
            self._emit_locked(
                record, OpportunityTransitionType.ENTRY_ATTEMPT, attempt.attempted_at,
                price=attempt.requested_price, quantity=attempt.requested_quantity,
                reason=attempt.reason, setup=attempt.setup,
            )

    def record_reclaim(self, trading_date: date, symbol: str, opportunity_id: str,
                       at: datetime, level: Decimal, *, confirmed: bool = False) -> None:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            raise KeyError("opportunity must be observed before recording a reclaim")
        with self._lock:
            record.latest_reclaim_level, record.latest_reclaim_at = level, at
            self._emit_locked(
                record,
                OpportunityTransitionType.RECLAIM_CONFIRMED if confirmed else OpportunityTransitionType.RECLAIM_STARTED,
                at, price=level,
            )

    def assess_new_structure(
        self, trading_date: date, symbol: str, opportunity_id: str, *,
        entry_anchor: Decimal, structural_stop: Decimal, spread_ok: bool,
        liquidity_ok: bool, freshness_ok: bool, position_quantity: Decimal = ZERO,
        working_entry: bool = False, lifecycle_count: int = 0,
        max_lifecycles: int = 3, higher_low: bool = False,
        reclaim: bool = False, momentum_reaccelerated: bool = False,
    ) -> NewStructureAssessment:
        record = self.get(trading_date, symbol, opportunity_id)
        structure = "RECLAIM" if reclaim else "HIGHER_LOW_CONTINUATION" if higher_low else "MOMENTUM_REACCELERATION" if momentum_reaccelerated else "PULLBACK_CONTINUATION"
        checks = (
            (record is not None and record.opportunity_state not in {OpportunityMemoryState.INVALIDATED, OpportunityMemoryState.CLOSED}, "OPPORTUNITY_INACTIVE"),
            (position_quantity <= ZERO, "POSITION_EXISTS"),
            (not working_entry, "WORKING_ENTRY_EXISTS"),
            (lifecycle_count < max_lifecycles, "LIFECYCLE_CAP"),
            (entry_anchor > structural_stop, "INVALID_STRUCTURAL_STOP"),
            (spread_ok, "SPREAD_BLOCKED"), (liquidity_ok, "LIQUIDITY_BLOCKED"),
            (freshness_ok, "STALE_MARKET_DATA"),
            (record is not None and record.original_entry_anchor != entry_anchor, "OLD_ENTRY_ANCHOR"),
            (record is not None and record.highest_price_since_start is not None, "NO_PRIOR_IMPULSE"),
            (higher_low or reclaim or momentum_reaccelerated, "NO_NEW_STRUCTURE"),
        )
        failed = next((reason for passed, reason in checks if not passed), None)
        risk = entry_anchor - structural_stop
        return NewStructureAssessment(
            failed is None, structure, failed, entry_anchor, structural_stop, risk,
        )

    def assess_continuation_structure(
        self, trading_date: date, symbol: str, opportunity_id: str, *,
        entry_anchor: Decimal, structural_stop: Decimal, spread_ok: bool,
        liquidity_ok: bool, freshness_ok: bool,
        classification: PullbackClassification,
        structure: str,
        structure_established: bool,
        position_quantity: Decimal = ZERO, working_entry: bool = False,
        lifecycle_count: int = 0, max_lifecycles: int = 3,
        reclaim_level: Decimal | None = None,
    ) -> ContinuationStructureAssessment:
        """Validate a new continuation thesis without authorizing an order.

        Completed-bar code owns the structural facts and classification.  This
        method only applies continuity and existing execution-safety facts;
        therefore a single live price cannot manufacture a pullback.
        """
        record = self.get(trading_date, symbol, opportunity_id)
        checks = (
            (record is not None and record.opportunity_state not in {
                OpportunityMemoryState.INVALIDATED, OpportunityMemoryState.CLOSED,
            }, "OPPORTUNITY_INACTIVE"),
            (position_quantity <= ZERO, "POSITION_EXISTS"),
            (not working_entry, "WORKING_ENTRY_EXISTS"),
            (lifecycle_count < max_lifecycles, "LIFECYCLE_CAP"),
            (entry_anchor > structural_stop, "INVALID_STRUCTURAL_STOP"),
            (spread_ok, "SPREAD_BLOCKED"),
            (liquidity_ok, "LIQUIDITY_BLOCKED"),
            (freshness_ok, "STALE_MARKET_DATA"),
            (record is not None and record.original_entry_anchor != entry_anchor,
             "OLD_ENTRY_ANCHOR"),
            (record is not None and record.highest_price_since_start is not None,
             "NO_PRIOR_IMPULSE"),
            (record is not None and record.post_peak_pullback_low is not None,
             "NO_PULLBACK_CONTEXT"),
            (structure_established, "STRUCTURE_NOT_ESTABLISHED"),
            (classification in {
                PullbackClassification.HEALTHY,
                PullbackClassification.DEEP_BUT_VALID,
            }, "PULLBACK_FAILED"),
            (bool(structure.strip()), "NO_NEW_STRUCTURE"),
        )
        failed = next((reason for passed, reason in checks if not passed), None)
        risk = entry_anchor - structural_stop
        return ContinuationStructureAssessment(
            failed is None, structure, classification, failed,
            entry_anchor, structural_stop, reclaim_level, risk,
        )

    def assess_live_trigger(
        self, trading_date: date, symbol: str, opportunity_id: str, *,
        entry_anchor: Decimal, structural_stop: Decimal, trigger_price: Decimal,
        live_price: Decimal, spread_ok: bool, liquidity_ok: bool,
        freshness_ok: bool, position_quantity: Decimal = ZERO,
        working_entry: bool = False, lifecycle_count: int = 0,
        max_lifecycles: int = 3, higher_low: bool = False,
        reclaim: bool = False, momentum_reaccelerated: bool = False,
    ) -> NewStructureAssessment:
        """Assess only the final trigger from a fresh observation.

        The record must already contain a prior impulse and pullback/reclaim
        context.  Consequently a single tick cannot manufacture a structure.
        """
        assessment = self.assess_new_structure(
            trading_date, symbol, opportunity_id,
            entry_anchor=entry_anchor, structural_stop=structural_stop,
            spread_ok=spread_ok, liquidity_ok=liquidity_ok,
            freshness_ok=freshness_ok, position_quantity=position_quantity,
            working_entry=working_entry, lifecycle_count=lifecycle_count,
            max_lifecycles=max_lifecycles, higher_low=higher_low,
            reclaim=reclaim, momentum_reaccelerated=momentum_reaccelerated,
        )
        record = self.get(trading_date, symbol, opportunity_id)
        if assessment.eligible and record is not None:
            if record.structure_established_at is None:
                return NewStructureAssessment(False, assessment.structure, "NO_ESTABLISHED_STRUCTURE", entry_anchor, structural_stop, assessment.risk_per_share, trigger_price)
            if record.post_peak_pullback_low is None:
                return NewStructureAssessment(False, assessment.structure, "NO_ESTABLISHED_PULLBACK", entry_anchor, structural_stop, assessment.risk_per_share, trigger_price)
            if live_price < trigger_price:
                return NewStructureAssessment(False, assessment.structure, "LIVE_TRIGGER_NOT_REACHED", entry_anchor, structural_stop, assessment.risk_per_share, trigger_price)
            if any(a.requested_price == entry_anchor and a.result in {"AUTHORIZED", "NEW_STRUCTURAL_ENTRY"} for a in record.attempts):
                return NewStructureAssessment(False, assessment.structure, "DUPLICATE_LIVE_TRIGGER", entry_anchor, structural_stop, assessment.risk_per_share, trigger_price)
        return NewStructureAssessment(assessment.eligible, assessment.structure, assessment.reason, assessment.entry_anchor, assessment.structural_stop, assessment.risk_per_share, trigger_price)

    def record_structure_established(self, trading_date: date, symbol: str, opportunity_id: str,
                                     at: datetime, *, reason: str = "COMPLETED_BAR_STRUCTURE") -> None:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            raise KeyError("opportunity must be observed before recording structure")
        with self._lock:
            if record.structure_established_at is None:
                record.structure_established_at = at
                self._emit_locked(record, OpportunityTransitionType.STRUCTURE_ESTABLISHED, at, reason=reason)

    def record_live_trigger(self, trading_date: date, symbol: str, opportunity_id: str,
                            at: datetime, *, price: Decimal,
                            anchor: Decimal | None = None) -> bool:
        record = self.get(trading_date, symbol, opportunity_id)
        if record is None:
            raise KeyError("opportunity must be observed before recording a live trigger")
        with self._lock:
            if record.live_trigger_first_seen_at is not None and record.live_trigger_anchor == anchor:
                return False
            record.live_trigger_first_seen_at = at
            record.live_trigger_anchor = anchor
            self._emit_locked(record, OpportunityTransitionType.LIVE_TRIGGER_FIRST_SEEN, at, price=price)
            return True

    def _emit_locked(self, record, kind, at, *, price=None, quantity=None, reason=None, setup=None):
        event = OpportunityTransition(kind, at, record.opportunity_id, record.symbol, record.trading_date, reason, setup, price, quantity)
        if record.transitions and record.transitions[-1].kind is kind and record.transitions[-1].reason == reason:
            return None
        record.transitions.append(event)
        record.last_meaningful_change_at = at
        return event

    @staticmethod
    def _key(trading_date: date, symbol: str, opportunity_id: str):
        if not str(opportunity_id).strip():
            raise ValueError("opportunity_id is required")
        normalized = str(symbol).strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return trading_date, normalized, str(opportunity_id).strip()


class AsyncOpportunityMemoryWriter:
    """Bounded non-blocking transition writer with explicit degradation."""

    def __init__(self, sink: Callable[[OpportunityTransition], None], *, capacity: int = 4096) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._sink = sink
        self._queue: Queue[OpportunityTransition] = Queue(maxsize=capacity)
        self._stop = Event()
        self._lock = Lock()
        self._dropped = self._coalesced = 0
        self._fatal: BaseException | None = None
        self._thread = Thread(target=self._run, name="opportunity-memory-writer", daemon=True)
        self._thread.start()

    def submit(self, event: OpportunityTransition) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except Full:
            with self._lock:
                self._dropped += 1
            return False

    def close(self) -> None:
        self._queue.join()
        self._stop.set()
        self._thread.join(timeout=2.0)

    def metrics(self) -> dict[str, int | bool]:
        with self._lock:
            return {"queue_depth": self._queue.qsize(), "dropped": self._dropped, "coalesced": self._coalesced, "healthy": self._fatal is None}

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                event = self._queue.get(timeout=0.1)
            except Exception:
                continue
            try:
                self._sink(event)
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
            finally:
                self._queue.task_done()


__all__ = [
    "AsyncOpportunityMemoryWriter", "ContinuationStructureAssessment",
    "EntryAttemptSummary", "NewStructureAssessment", "OpportunityMemory",
    "OpportunityMemoryRecord", "OpportunityMemoryState", "OpportunityTransition",
    "OpportunityTransitionType", "PullbackClassification",
]
