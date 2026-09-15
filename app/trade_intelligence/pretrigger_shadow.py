"""PAPER-only adaptive pretrigger shadow intelligence.

This module deliberately produces immutable research records only.  Its
contracts contain no order, broker, portfolio, or risk-reservation port.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Iterable, Mapping

from app.momentum_scanner.models import CatalystStatus
from app.strategies.warrior_momentum.forward_models import (
    CAPTURE_SCHEMA_VERSION,
    CaptureRecord,
    CaptureRecordType,
    PointInTimeObservation,
    canonical_json,
)


SHADOW_VERSION = "ADAPTIVE_PRETRIGGER_ENTRY_V1_SHADOW"
STANDARD_PATH = "STANDARD_PRETRIGGER_SHADOW"
EXCEPTIONAL_PATH = "EXCEPTIONAL_MOMENTUM_OVERRIDE_SHADOW"

STANDARD_STRATEGIES = frozenset({"HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT"})
EXTENDED_LOCATIONS = frozenset({
    "SLIGHTLY_EXTENDED", "MODERATELY_EXTENDED", "HEAVILY_EXTENDED",
    "CHASE_RISK", "TOO_EXTENDED",
})

ZERO = Decimal("0")
HUNDRED = Decimal("100")
STANDARD_RVOL_FLOOR = Decimal("2.0")
STANDARD_DOLLAR_VOLUME_FLOOR = Decimal("500000")
STANDARD_SPREAD_CEILING = Decimal("1.60")
STANDARD_DISTANCE_R_CEILING = Decimal("0.50")
STANDARD_DISTANCE_PERCENT_CEILING = Decimal("1.00")
HYPOTHETICAL_PROBE_RISK_FRACTION = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class PretriggerShadowCandidate:
    """A taxonomy candidate that is structurally unable to authorize action."""

    shadow_version: str
    symbol: str
    session: str
    trading_date: str
    opportunity_id: str
    strategy: str
    taxonomy_stage: str
    structural_provenance: str
    structural_anchor: str | None
    detector_episode_id: str | None
    current_price: Decimal
    trigger: Decimal | None
    structural_stop: Decimal | None
    trigger_source: str
    stop_source: str
    memberships: tuple[str, ...]
    setup_score: Decimal | None
    momentum_score: Decimal | None
    relative_volume: Decimal | None
    current_day_dollar_volume: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    spread_dollars: Decimal | None
    spread_percent: Decimal | None
    quote_freshness_seconds: Decimal | None
    quote_provenance: str | None
    entry_location: str
    entry_location_assessment: str
    historical_confidence: str
    historical_validation_passed: bool
    catalyst_status: str
    catalyst_type: str | None
    catalyst_timestamp: datetime | None
    catalyst_source: str | None
    catalyst_fresh: bool
    tradable: bool
    structurally_invalidated: bool
    lifecycle_conflicts: tuple[str, ...]
    volume_acceleration: Decimal | None
    relative_volume_acceleration: Decimal | None
    current_day_dollar_volume_acceleration: Decimal | None
    spread_trend: str
    price_velocity_percent: Decimal | None
    distance_from_hod_percent: Decimal | None
    opportunity_age_seconds: Decimal | None
    execution_authority: bool = False
    risk_authorization: bool = False
    real_risk_reserved: bool = False
    order_request: bool = False
    broker_placement: bool = False

    def __post_init__(self) -> None:
        if any((self.execution_authority, self.risk_authorization,
                self.real_risk_reserved, self.order_request,
                self.broker_placement)):
            raise ValueError("pretrigger shadow candidates cannot authorize execution")
        if not self.opportunity_id:
            raise ValueError("stable opportunity identity is required")


@dataclass(frozen=True, slots=True)
class PretriggerShadowEvaluation:
    candidate: PretriggerShadowCandidate
    classification: str
    paths: tuple[str, ...]
    reasons: tuple[str, ...]
    standard_would_authorize_probe: bool
    exceptional_override_candidate: bool
    catalyst_override_candidate: bool
    exceptional_evidence_strength: str
    valid_geometry: bool
    risk_per_share: Decimal | None
    pretrigger_distance: Decimal | None
    pretrigger_distance_r: Decimal | None
    pretrigger_distance_percent: Decimal | None
    distance_r_bucket: str
    distance_percent_bucket: str
    dollar_volume_bucket: str
    spread_bucket: str
    spread_to_risk: Decimal | None
    spread_to_historical_move: Decimal | None
    hypothetical_probe_risk_fraction: Decimal | None
    execution_authority: bool = False
    real_risk_reserved: bool = False
    real_order_created: bool = False
    broker_placement_possible: bool = False

    def __post_init__(self) -> None:
        if any((self.execution_authority, self.real_risk_reserved,
                self.real_order_created, self.broker_placement_possible)):
            raise ValueError("shadow evaluations cannot authorize execution")


@dataclass(slots=True)
class _ForwardState:
    opportunity_id: str
    decision_at: datetime
    decision_price: Decimal
    trigger: Decimal
    stop: Decimal
    risk_per_share: Decimal
    maximum_price: Decimal
    minimum_price: Decimal
    classification: str
    structural_provenance: str
    probe_recorded: bool = False
    confirmation_at: datetime | None = None
    confirmation_price: Decimal | None = None
    stop_at: datetime | None = None
    invalidated_at: datetime | None = None
    half_r_at: datetime | None = None
    one_r_at: datetime | None = None
    two_r_at: datetime | None = None
    extension_before_confirmation: bool = False
    last_outcome_minute: datetime | None = None


class AdaptivePretriggerShadowIntelligence:
    """Bounded observer over DI-2 results and point-in-time market evidence."""

    def __init__(self, writer: object, *, state_limit: int = 1000,
                 stale_after_seconds: Decimal = Decimal("5"),
                 configuration_fingerprint: str | None = None,
                 store: object | None = None) -> None:
        if state_limit <= 0 or stale_after_seconds < 0:
            raise ValueError("shadow bounds must be positive")
        self._writer = writer
        self._state_limit = state_limit
        self._stale_after = stale_after_seconds
        self._configuration_fingerprint = configuration_fingerprint
        self._states: OrderedDict[str, _ForwardState] = OrderedDict()
        self._last_evaluation: OrderedDict[str, tuple[object, ...]] = OrderedDict()
        self._last_market: OrderedDict[
            str, tuple[Decimal | None, Decimal | None, Decimal | None]
        ] = OrderedDict()
        self._recover(store)

    @property
    def state_bound(self) -> int:
        return self._state_limit

    def observe(
        self, *, value: PointInTimeObservation, result: object,
        candidate: object, lifecycle_conflicts: Iterable[str] = (),
        conventional_signal: object | None = None,
    ) -> PretriggerShadowEvaluation | None:
        evaluation = self.evaluate(
            value=value, result=result, candidate=candidate,
            lifecycle_conflicts=lifecycle_conflicts,
        )
        if evaluation is None:
            return None
        observed_at = value.evaluation_timestamp or value.observation.timestamp
        self._record_evaluation(evaluation, observed_at)
        state = self._states.get(evaluation.candidate.opportunity_id)
        if (
            state is None
            and evaluation.classification != "NO_EARLY_ENTRY"
            and evaluation.valid_geometry
        ):
            state = _ForwardState(
                evaluation.candidate.opportunity_id, observed_at,
                evaluation.candidate.current_price,
                evaluation.candidate.trigger, evaluation.candidate.structural_stop,
                evaluation.risk_per_share, evaluation.candidate.current_price,
                evaluation.candidate.current_price, evaluation.classification,
                evaluation.candidate.structural_provenance,
            )
            self._remember(self._states, state.opportunity_id, state)
        if (
            state is not None
            and evaluation.hypothetical_probe_risk_fraction is not None
            and not state.probe_recorded
        ):
            # A rejected observation may improve into the prospective envelope.
            # Start the hypothetical probe's forward basis only at that point.
            state.decision_at = observed_at
            state.decision_price = evaluation.candidate.current_price
            state.trigger = evaluation.candidate.trigger
            state.stop = evaluation.candidate.structural_stop
            state.risk_per_share = evaluation.risk_per_share
            state.maximum_price = evaluation.candidate.current_price
            state.minimum_price = evaluation.candidate.current_price
            state.classification = evaluation.classification
            state.probe_recorded = True
            self._submit(self._stable_record(
                CaptureRecordType.SHADOW_LATCHED_PLAN, evaluation.candidate,
                observed_at, "PRETRIGGER_SHADOW_PROBE_RECORDED",
                {**self._payload(evaluation),
                 "opportunity_id": evaluation.candidate.opportunity_id,
                 "hypothetical_filled_probe_risk_fraction": "0.25",
                 "normal_trade_risk_fraction": "1",
                 "real_risk_reserved": False,
                 "execution_authority": False},
            ))
        if state is not None:
            self._update_outcome(state, evaluation, value, conventional_signal)
        return evaluation

    def evaluate(
        self, *, value: PointInTimeObservation, result: object,
        candidate: object, lifecycle_conflicts: Iterable[str] = (),
    ) -> PretriggerShadowEvaluation | None:
        opportunity_id = str(getattr(result, "opportunity_id", "") or "")
        if not opportunity_id:
            return None
        rows = tuple(
            row for row in getattr(result, "readiness_memberships", ())
            if str(row.get("state", "")).upper() == "TRIGGER_ARMED"
        )
        memberships = tuple(dict.fromkeys(
            str(item) for item in getattr(result, "recognized_memberships", ())
        ))
        selected = next(
            (row for row in rows if str(row.get("strategy")) in STANDARD_STRATEGIES),
            rows[0] if rows else None,
        )
        prior = self._states.get(opportunity_id)
        strategy = str(
            (selected or {}).get("strategy")
            or getattr(result, "primary_strategy", "") or "UNKNOWN"
        )
        observation = value.observation
        current = _decimal(observation.price)
        if current is None or current <= ZERO:
            return None
        trigger = _decimal((selected or {}).get("trigger"))
        if trigger is None:
            trigger = _decimal(getattr(result, "trigger_price", None))
        if trigger is None and prior is not None:
            trigger = prior.trigger
        stop = _decimal((selected or {}).get("structural_stop"))
        if stop is None:
            stop = _decimal(getattr(result, "structural_stop", None))
        if stop is None and prior is not None:
            stop = prior.stop
        provenance = str((selected or {}).get("structural_provenance") or "")
        if not provenance and prior is not None:
            provenance = prior.structural_provenance
        stage = str(getattr(result, "setup_stage", "NO_SETUP"))
        setup = getattr(candidate, "setup", None)
        setup_score = _decimal(getattr(setup, "score", None))
        momentum_score = _decimal(getattr(getattr(candidate, "score", None), "total", None))
        rvol = _decimal(getattr(candidate, "relative_volume", None))
        if rvol is None:
            rvol = _decimal(getattr(result, "relative_volume", None))
        dollar_volume = _decimal(getattr(candidate, "dollar_volume", None))
        if dollar_volume is None:
            current_volume = _decimal(observation.current_volume)
            dollar_volume = None if current_volume is None else current * current_volume
        bid, ask = _decimal(observation.bid), _decimal(observation.ask)
        spread = None if bid is None or ask is None or ask < bid else ask - bid
        spread_percent = None if spread is None else spread / current * HUNDRED
        freshness = _decimal(value.quote_freshness_seconds)
        quote_authoritative = bool(
            bid is not None and ask is not None
            and str(getattr(value, "quote_provenance", "")).strip()
        )
        fresh = bool(
            freshness is not None and ZERO <= freshness <= self._stale_after
            and value.last_price_freshness_seconds is not None
            and ZERO <= Decimal(value.last_price_freshness_seconds) <= self._stale_after
        )
        catalyst_status = str(getattr(observation.catalyst_status, "value",
                                      observation.catalyst_status)).upper()
        catalyst_timestamp = (
            observation.catalyst_published_at or value.catalyst_event_timestamp
        )
        catalyst_source = observation.catalyst_source or value.catalyst_source
        catalyst_verified = catalyst_status == CatalystStatus.TRUE.value
        catalyst_fresh = bool(
            catalyst_verified and catalyst_timestamp is not None
            and catalyst_timestamp <= (value.evaluation_timestamp or observation.timestamp)
        )
        volume_acceleration, price_velocity = _motion(value)
        invalidated = str(getattr(result, "setup_state", "")).upper() == "INVALIDATED"
        conflicts = tuple(dict.fromkeys(str(item) for item in lifecycle_conflicts))
        historical_validation = _historical_validation(
            getattr(result, "setup_evidence", ())
        )
        previous_market = self._last_market.get(opportunity_id)
        prior_dollar, prior_spread, prior_rvol = (
            (None, None, None) if previous_market is None else previous_market
        )
        dollar_acceleration = _ratio(dollar_volume, prior_dollar)
        rvol_acceleration = _ratio(rvol, prior_rvol)
        spread_trend = _trend(spread_percent, prior_spread)
        self._remember(
            self._last_market, opportunity_id,
            (dollar_volume, spread_percent, rvol),
        )
        entry_location, entry_assessment = _entry_location(trigger, current)
        reported_location = str(getattr(result, "entry_location", ""))
        if reported_location in EXTENDED_LOCATIONS:
            entry_location = reported_location
            entry_assessment = str(
                getattr(result, "entry_assessment", "CHASE_RISK")
            )
        candidate_contract = PretriggerShadowCandidate(
            SHADOW_VERSION, str(observation.symbol).strip().upper(),
            str(getattr(value, "session", "UNKNOWN")),
            str(getattr(result, "trading_date", "") or observation.timestamp.date()),
            opportunity_id, strategy, stage, provenance,
            getattr(result, "structural_anchor", None),
            None if selected is None else str(selected.get("detector_episode_id") or "") or None,
            current, trigger, stop,
            str((selected or {}).get("trigger_source") or "TAXONOMY_COMPLETED_BAR"),
            str((selected or {}).get("stop_source") or "TAXONOMY_STRUCTURAL_STOP"),
            memberships, setup_score, momentum_score, rvol, dollar_volume,
            bid, ask, spread, spread_percent, freshness,
            str(getattr(value, "quote_provenance", "") or "") or None,
            entry_location, entry_assessment,
            str(getattr(result, "confidence", "MISSING_CONTEXT")),
            historical_validation, catalyst_status,
            str(getattr(observation.catalyst, "value", observation.catalyst)),
            catalyst_timestamp, catalyst_source, catalyst_fresh,
            bool(observation.tradable and not observation.halted), invalidated,
            conflicts, volume_acceleration, rvol_acceleration,
            dollar_acceleration, spread_trend, price_velocity,
            _decimal(getattr(result, "distance_from_hod_percent", None)),
            _decimal(getattr(result, "opportunity_age_seconds", None)),
        )
        risk = None if stop is None else current - stop
        distance = None if trigger is None else trigger - current
        valid_geometry = bool(
            stop is not None and trigger is not None and risk is not None
            and risk > ZERO and current <= trigger
        )
        distance_r = None if not valid_geometry else distance / risk
        distance_pct = None if not valid_geometry else distance / current * HUNDRED
        spread_to_risk = None if spread is None or risk is None or risk <= ZERO else spread / risk
        historical_move = _historical_move(getattr(result, "setup_evidence", ()))
        spread_to_move = (
            None if spread_percent is None or historical_move is None or historical_move <= ZERO
            else spread_percent / historical_move
        )
        reasons: list[str] = []
        _fail(reasons, strategy in STANDARD_STRATEGIES, "WRONG_STRATEGY")
        _fail(reasons, bool(rows) and stage == "TRIGGER_READY", "NOT_TRIGGER_READY")
        _fail(reasons, provenance.startswith("taxonomy-structural-provenance-v1|"), "INVALID_PROVENANCE")
        _fail(reasons, valid_geometry, "INVALID_GEOMETRY")
        _fail(reasons, distance_r is not None and distance_r <= STANDARD_DISTANCE_R_CEILING, "TOO_FAR_R")
        _fail(reasons, distance_pct is not None and distance_pct <= STANDARD_DISTANCE_PERCENT_CEILING, "TOO_FAR_PERCENT")
        _fail(reasons, momentum_score is not None and momentum_score >= Decimal("55"), "MOMENTUM_TOO_LOW")
        _fail(reasons, setup_score is not None and setup_score >= Decimal("55"), "SETUP_SCORE_TOO_LOW")
        _fail(reasons, rvol is not None and rvol >= STANDARD_RVOL_FLOOR, "RVOL_TOO_LOW")
        if dollar_volume is None or dollar_volume < STANDARD_DOLLAR_VOLUME_FLOOR:
            reasons.append("DOLLAR_VOLUME_BELOW_500K")
        if dollar_volume is None or dollar_volume < Decimal("250000"):
            reasons.append("DOLLAR_VOLUME_BELOW_250K")
        _fail(reasons, fresh, "STALE_MARKET_DATA")
        _fail(reasons, quote_authoritative, "NO_AUTHORITATIVE_QUOTE")
        if spread_percent is None or spread_percent > STANDARD_SPREAD_CEILING:
            reasons.append("SPREAD_ABOVE_1_60")
        if spread_percent is None or spread_percent > Decimal("2.00"):
            reasons.append("SPREAD_ABOVE_2_00")
        _fail(reasons, candidate_contract.tradable, "NOT_TRADABLE")
        _fail(reasons, candidate_contract.entry_location not in EXTENDED_LOCATIONS, "ENTRY_EXTENDED")
        _fail(reasons, not invalidated, "STRUCTURE_INVALIDATED")
        reasons.extend(conflicts)
        reasons.append(
            "CATALYST_VERIFIED" if catalyst_verified else
            "CATALYST_UNAVAILABLE" if catalyst_status in {"UNAVAILABLE", "UNKNOWN"}
            else "CATALYST_STALE" if catalyst_timestamp is not None else "CATALYST_UNAVAILABLE"
        )
        if not historical_validation:
            reasons.append("HISTORICAL_VALIDATION_UNAVAILABLE")
        standard_blockers = {
            "WRONG_STRATEGY", "NOT_TRIGGER_READY", "INVALID_PROVENANCE",
            "INVALID_GEOMETRY", "TOO_FAR_R", "TOO_FAR_PERCENT",
            "MOMENTUM_TOO_LOW", "SETUP_SCORE_TOO_LOW", "RVOL_TOO_LOW",
            "DOLLAR_VOLUME_BELOW_500K", "STALE_MARKET_DATA",
            "NO_AUTHORITATIVE_QUOTE", "SPREAD_ABOVE_1_60", "NOT_TRADABLE",
            "ENTRY_EXTENDED", "STRUCTURE_INVALIDATED", "ACTIVE_POSITION",
            "WORKING_ORDER", "PARTIAL_FILL_OR_LIFECYCLE", "DURABLY_CONSUMED",
        }
        standard_eligible = not (set(reasons) & standard_blockers)
        exceptional_base_blockers = standard_blockers - {
            "DOLLAR_VOLUME_BELOW_500K", "SPREAD_ABOVE_1_60",
            "MOMENTUM_TOO_LOW", "SETUP_SCORE_TOO_LOW", "RVOL_TOO_LOW",
        }
        exceptional_strong = bool(
            not (set(reasons) & exceptional_base_blockers)
            and momentum_score is not None and momentum_score >= Decimal("70")
            and setup_score is not None and setup_score >= Decimal("70")
            and rvol is not None and rvol >= Decimal("3")
            and (
                catalyst_fresh
                or (
                    volume_acceleration is not None and volume_acceleration >= Decimal("1.5")
                    and price_velocity is not None and price_velocity > ZERO
                    and len(memberships) >= 2
                )
            )
        )
        outside_standard = bool(
            dollar_volume is not None and dollar_volume < STANDARD_DOLLAR_VOLUME_FLOOR
            or spread_percent is not None and spread_percent > STANDARD_SPREAD_CEILING
        )
        exceptional = exceptional_strong and outside_standard
        if exceptional:
            reasons.append("EXCEPTIONAL_MOMENTUM_EVIDENCE")
        if standard_eligible:
            classification = "STANDARD_ELIGIBLE"
        elif exceptional and dollar_volume is not None and dollar_volume < Decimal("250000"):
            classification = "VERY_LOW_LIQUIDITY_EXCEPTION"
        elif exceptional and spread_percent is not None and spread_percent > Decimal("2.00"):
            classification = "EXTREME_SPREAD_EXCEPTION"
        elif exceptional:
            classification = "EXCEPTIONAL_OVERRIDE_CANDIDATE"
        elif strategy in STANDARD_STRATEGIES and rows:
            classification = "STANDARD_REJECTED"
        else:
            classification = "NO_EARLY_ENTRY"
        paths = (STANDARD_PATH,) if standard_eligible else (
            (EXCEPTIONAL_PATH,) if exceptional else ()
        )
        probe = (
            HYPOTHETICAL_PROBE_RISK_FRACTION
            if classification in {
                "STANDARD_ELIGIBLE", "EXCEPTIONAL_OVERRIDE_CANDIDATE",
                "VERY_LOW_LIQUIDITY_EXCEPTION", "EXTREME_SPREAD_EXCEPTION",
            } else None
        )
        return PretriggerShadowEvaluation(
            candidate_contract, classification, paths, tuple(dict.fromkeys(reasons)),
            standard_eligible, exceptional, catalyst_fresh and exceptional,
            "EXCEPTIONAL" if exceptional else "STANDARD" if standard_eligible else "INSUFFICIENT",
            valid_geometry, risk if valid_geometry else None,
            distance if valid_geometry else None, distance_r, distance_pct,
            _distance_r_bucket(distance_r), _distance_percent_bucket(distance_pct),
            _dollar_volume_bucket(dollar_volume), _spread_bucket(spread_percent),
            spread_to_risk, spread_to_move, probe,
        )

    def memory_metrics(self) -> Mapping[str, int]:
        return {
            "active_shadow_contexts": len(self._states),
            "evaluation_dedupe_contexts": len(self._last_evaluation),
            "market_evolution_contexts": len(self._last_market),
            "maximum_contexts": self._state_limit,
        }

    def _recover(self, store: object | None) -> None:
        """Load at most the hot-state bound once; no detector-time reads."""
        loader = getattr(store, "latest_records", None)
        if not callable(loader):
            return
        try:
            records = loader(
                record_type=CaptureRecordType.SHADOW_LATCHED_PLAN,
                limit=self._state_limit,
            )
        except Exception:
            return
        for record in reversed(tuple(records)):
            try:
                payload = record.payload
                if payload.get("shadow_version") != SHADOW_VERSION:
                    continue
                candidate = payload["candidate"]
                opportunity_id = str(payload["opportunity_id"])
                current = Decimal(candidate["current_price"])
                trigger = Decimal(candidate["trigger"])
                stop = Decimal(candidate["structural_stop"])
                risk = Decimal(payload["risk_per_share"])
                self._remember(self._states, opportunity_id, _ForwardState(
                    opportunity_id, record.timestamp, current, trigger, stop,
                    risk, current, current, str(payload["classification"]),
                    str(candidate["structural_provenance"]), True,
                ))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
        try:
            outcomes = loader(
                record_type=CaptureRecordType.SHADOW_OUTCOME,
                limit=self._state_limit,
            )
        except Exception:
            return
        restored: set[str] = set()
        for record in outcomes:
            try:
                payload = record.payload
                opportunity_id = str(payload["opportunity_id"])
                if (
                    payload.get("shadow_version") != SHADOW_VERSION
                    or opportunity_id in restored
                ):
                    continue
                state = self._states.get(opportunity_id)
                if state is None:
                    continue
                state.maximum_price = state.decision_price + Decimal(payload["mfe"])
                state.minimum_price = state.decision_price + Decimal(payload["mae"])
                state.confirmation_at = _time(payload.get("confirmation_at"))
                state.confirmation_price = _decimal(payload.get("confirmation_price"))
                state.stop_at = _time(payload.get("stop_at"))
                state.invalidated_at = _time(payload.get("invalidated_at"))
                state.half_r_at = _time(payload.get("time_to_half_r"))
                state.one_r_at = _time(payload.get("time_to_one_r"))
                state.two_r_at = _time(payload.get("time_to_two_r"))
                state.extension_before_confirmation = bool(
                    payload.get("extension_before_confirmation", False)
                )
                state.last_outcome_minute = record.timestamp.replace(
                    second=0, microsecond=0,
                )
                restored.add(opportunity_id)
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue

    def _record_evaluation(self, evaluation: PretriggerShadowEvaluation,
                           observed_at: datetime) -> None:
        key = evaluation.candidate.opportunity_id
        minute = observed_at.replace(second=0, microsecond=0)
        signature = (
            minute, evaluation.classification, evaluation.distance_r_bucket,
            evaluation.distance_percent_bucket, evaluation.dollar_volume_bucket,
            evaluation.spread_bucket, evaluation.candidate.catalyst_status,
        )
        if self._last_evaluation.get(key) == signature:
            return
        self._remember(self._last_evaluation, key, signature)
        events = ["PRETRIGGER_CANDIDATE_SURFACED", "PRETRIGGER_SHADOW_EVALUATED"]
        events.append(
            "PRETRIGGER_STANDARD_ELIGIBLE"
            if evaluation.standard_would_authorize_probe else
            "PRETRIGGER_EXCEPTIONAL_CANDIDATE"
            if evaluation.exceptional_override_candidate else
            "PRETRIGGER_STANDARD_REJECTED"
        )
        self._submit(CaptureRecord.create(
            CaptureRecordType.SHADOW_EVALUATION,
            evaluation.candidate.symbol, observed_at,
            {**self._payload(evaluation), "diagnostics": events},
            identity_parts=(SHADOW_VERSION, key, minute.isoformat()),
        ))

    def _update_outcome(
        self, state: _ForwardState, evaluation: PretriggerShadowEvaluation,
        value: PointInTimeObservation, conventional_signal: object | None,
    ) -> None:
        observed_at = value.evaluation_timestamp or value.observation.timestamp
        prices = [evaluation.candidate.current_price]
        lows = [evaluation.candidate.current_price]
        for bar in value.bars:
            completed_at = bar.timestamp + timedelta(minutes=1)
            if completed_at >= state.decision_at:
                prices.append(Decimal(bar.high))
                lows.append(Decimal(bar.low))
                if state.stop_at is None and Decimal(bar.low) <= state.stop:
                    state.stop_at = observed_at
        state.maximum_price = max(state.maximum_price, *prices)
        state.minimum_price = min(state.minimum_price, *lows)
        stage = evaluation.candidate.taxonomy_stage
        if state.confirmation_at is None and state.probe_recorded and (
            stage in {"TRIGGERED", "POST_TRIGGER_EXTENDED"}
            or conventional_signal is not None
        ):
            state.confirmation_at = observed_at
            state.confirmation_price = evaluation.candidate.current_price
            remaining = max(ZERO, Decimal("1") - HYPOTHETICAL_PROBE_RISK_FRACTION)
            add_eligible = bool(
                evaluation.candidate.spread_percent is not None
                and evaluation.candidate.spread_percent <= STANDARD_SPREAD_CEILING
                and evaluation.candidate.current_day_dollar_volume is not None
                and evaluation.candidate.current_day_dollar_volume >= STANDARD_DOLLAR_VOLUME_FLOOR
                and evaluation.candidate.entry_location not in EXTENDED_LOCATIONS
                and not evaluation.candidate.lifecycle_conflicts
            )
            self._submit(self._stable_record(
                CaptureRecordType.SHADOW_POLICY_RESULT, evaluation.candidate,
                observed_at, "PRETRIGGER_CONFIRMATION_OBSERVED",
                {"shadow_version": SHADOW_VERSION, "opportunity_id": state.opportunity_id,
                 "confirmation_add": "SHADOW_ONLY", "confirmation_add_eligible": add_eligible,
                 "hypothetical_filled_probe_risk_fraction": "0.25",
                 "remaining_hypothetical_risk_fraction": str(remaining),
                 "total_risk_cap_fraction": "1", "execution_authority": False},
            ))
        if state.stop_at is None and evaluation.candidate.current_price <= state.stop:
            state.stop_at = observed_at
        if state.invalidated_at is None and evaluation.candidate.structurally_invalidated:
            state.invalidated_at = observed_at
        for multiple, attribute in (
            (Decimal("0.5"), "half_r_at"), (Decimal("1"), "one_r_at"),
            (Decimal("2"), "two_r_at"),
        ):
            if getattr(state, attribute) is None and state.maximum_price >= state.decision_price + state.risk_per_share * multiple:
                setattr(state, attribute, observed_at)
        if state.confirmation_at is None and evaluation.candidate.entry_location in EXTENDED_LOCATIONS:
            state.extension_before_confirmation = True
        minute = observed_at.replace(second=0, microsecond=0)
        if state.last_outcome_minute == minute:
            return
        state.last_outcome_minute = minute
        mfe = state.maximum_price - state.decision_price
        mae = state.minimum_price - state.decision_price
        payload = {
            "diagnostic": "PRETRIGGER_OUTCOME_UPDATED", "shadow_version": SHADOW_VERSION,
            "opportunity_id": state.opportunity_id, "classification_at_decision": state.classification,
            "decision_at": state.decision_at, "decision_price": state.decision_price,
            "trigger_at_decision": state.trigger, "structural_stop_at_decision": state.stop,
            "risk_per_share": state.risk_per_share, "confirmation_at": state.confirmation_at,
            "confirmation_price": state.confirmation_price, "stop_at": state.stop_at,
            "invalidated_at": state.invalidated_at, "mfe": mfe, "mae": mae,
            "mfe_r": mfe / state.risk_per_share, "mae_r": mae / state.risk_per_share,
            "time_to_half_r": state.half_r_at, "time_to_one_r": state.one_r_at,
            "time_to_two_r": state.two_r_at,
            "stop_before_confirmation": bool(
                state.stop_at and (
                    state.confirmation_at is None or state.stop_at < state.confirmation_at
                )
            ),
            "one_r_before_confirmation": bool(
                state.one_r_at and (
                    state.confirmation_at is None or state.one_r_at < state.confirmation_at
                )
            ),
            "extension_before_confirmation": state.extension_before_confirmation,
            "current_spread_percent": evaluation.candidate.spread_percent,
            "current_dollar_volume": evaluation.candidate.current_day_dollar_volume,
            "current_relative_volume": evaluation.candidate.relative_volume,
            "catalyst_status_at_decision": evaluation.candidate.catalyst_status,
            "execution_authority": False,
        }
        self._submit(CaptureRecord.create(
            CaptureRecordType.SHADOW_OUTCOME, evaluation.candidate.symbol,
            observed_at, payload,
            identity_parts=(SHADOW_VERSION, state.opportunity_id, minute.isoformat()),
        ))

    def _stable_record(self, record_type: CaptureRecordType,
                       candidate: PretriggerShadowCandidate,
                       observed_at: datetime, event: str,
                       payload: Mapping[str, object]) -> CaptureRecord:
        body = {"diagnostic": event, **payload}
        if self._configuration_fingerprint is not None:
            body["configuration_fingerprint"] = self._configuration_fingerprint
        encoded = canonical_json(body)
        identity = sha256(
            f"{SHADOW_VERSION}|{candidate.opportunity_id}|{event}".encode()
        ).hexdigest()
        return CaptureRecord(
            CAPTURE_SCHEMA_VERSION, identity, record_type, candidate.symbol,
            observed_at, encoded,
        )

    def _submit(self, record: CaptureRecord) -> None:
        try:
            self._writer.submit(record)
        except Exception:
            # Research loss must not affect the PAPER control path.
            return

    @staticmethod
    def _payload(evaluation: PretriggerShadowEvaluation) -> dict[str, object]:
        return {"shadow_version": SHADOW_VERSION,
                "candidate": asdict(evaluation.candidate),
                "classification": evaluation.classification,
                "paths": evaluation.paths, "reasons": evaluation.reasons,
                "standard_would_authorize_probe": evaluation.standard_would_authorize_probe,
                "exceptional_override_candidate": evaluation.exceptional_override_candidate,
                "catalyst_override_candidate": evaluation.catalyst_override_candidate,
                "exceptional_evidence_strength": evaluation.exceptional_evidence_strength,
                "valid_geometry": evaluation.valid_geometry,
                "risk_per_share": evaluation.risk_per_share,
                "pretrigger_distance": evaluation.pretrigger_distance,
                "pretrigger_distance_r": evaluation.pretrigger_distance_r,
                "pretrigger_distance_percent": evaluation.pretrigger_distance_percent,
                "distance_r_bucket": evaluation.distance_r_bucket,
                "distance_percent_bucket": evaluation.distance_percent_bucket,
                "dollar_volume_bucket": evaluation.dollar_volume_bucket,
                "spread_bucket": evaluation.spread_bucket,
                "spread_to_risk": evaluation.spread_to_risk,
                "spread_to_historical_move": evaluation.spread_to_historical_move,
                "hypothetical_probe_risk_fraction": evaluation.hypothetical_probe_risk_fraction,
                "execution_authority": False, "real_risk_reserved": False,
                "real_order_created": False, "broker_placement_possible": False}

    def _remember(self, mapping: OrderedDict, key: str, value: object) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > self._state_limit:
            mapping.popitem(last=False)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _ratio(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None or previous <= ZERO:
        return None
    return current / previous


def _trend(current: Decimal | None, previous: Decimal | None) -> str:
    if current is None or previous is None:
        return "UNAVAILABLE"
    if current < previous:
        return "IMPROVING"
    if current > previous:
        return "WORSENING"
    return "UNCHANGED"


def _entry_location(
    trigger: Decimal | None, current: Decimal,
) -> tuple[str, str]:
    if trigger is None or trigger <= ZERO:
        return "INSUFFICIENT_DATA", "INSUFFICIENT_EVIDENCE"
    if current <= trigger:
        distance = (trigger - current) / current * HUNDRED
        return (
            ("APPROACHING_TRIGGER", "FAVORABLE_LOCATION")
            if distance <= Decimal("1")
            else ("PRE_TRIGGER", "ACCEPTABLE_LOCATION")
        )
    extension = (current - trigger) / trigger * HUNDRED
    if extension <= Decimal("0.5"):
        return "AT_TRIGGER", "FAVORABLE_LOCATION"
    if extension <= Decimal("1.5"):
        return "SLIGHTLY_EXTENDED", "LATE_ENTRY_RISK"
    if extension <= Decimal("3"):
        return "MODERATELY_EXTENDED", "CHASE_RISK"
    return "HEAVILY_EXTENDED", "CHASE_RISK"


def _fail(reasons: list[str], passed: bool, reason: str) -> None:
    if not passed:
        reasons.append(reason)


def _motion(value: PointInTimeObservation) -> tuple[Decimal | None, Decimal | None]:
    bars = tuple(value.bars)
    if len(bars) < 2:
        return None, None
    previous, current = bars[-2], bars[-1]
    volume = None if Decimal(previous.volume) <= ZERO else Decimal(current.volume) / Decimal(previous.volume)
    velocity = None if Decimal(previous.close) <= ZERO else (
        Decimal(current.close) / Decimal(previous.close) - Decimal("1")
    ) * HUNDRED
    return volume, velocity


def _historical_validation(rows: Iterable[Mapping[str, object]]) -> bool:
    return any(
        int(row.get("test_sample_count") or 0) >= 30
        and str(row.get("walk_forward_state", "")).upper() == "STABLE"
        and str(row.get("confidence_state", "")).upper() in {
            "STRONG_EVIDENCE", "MODERATE_EVIDENCE"
        }
        for row in rows
    )


def _historical_move(rows: Iterable[Mapping[str, object]]) -> Decimal | None:
    values = tuple(
        value for row in rows
        if (value := _decimal(row.get("median_mfe"))) is not None and value > ZERO
    )
    return max(values) if values else None


def _distance_r_bucket(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value <= Decimal("0.25"):
        return "<=0.25R"
    if value <= Decimal("0.50"):
        return ">0.25R <=0.50R"
    if value <= Decimal("1.00"):
        return ">0.50R <=1.00R"
    return ">1.00R"


def _distance_percent_bucket(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value <= Decimal("0.50"):
        return "<=0.50%"
    if value <= Decimal("1.00"):
        return ">0.50% <=1.00%"
    if value <= Decimal("2.00"):
        return ">1.00% <=2.00%"
    return ">2.00%"


def _dollar_volume_bucket(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value < Decimal("250000"):
        return "<$250,000"
    if value < Decimal("500000"):
        return "$250,000-<$500,000"
    if value < Decimal("1000000"):
        return "$500,000-<$1,000,000"
    if value < Decimal("2500000"):
        return "$1,000,000-<$2,500,000"
    if value < Decimal("5000000"):
        return "$2,500,000-<$5,000,000"
    return ">=$5,000,000"


def _spread_bucket(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value <= Decimal("1.00"):
        return "<=1.00%"
    if value <= Decimal("1.25"):
        return ">1.00% <=1.25%"
    if value <= Decimal("1.60"):
        return ">1.25% <=1.60%"
    if value <= Decimal("2.00"):
        return ">1.60% <=2.00%"
    return ">2.00%"


__all__ = [
    "AdaptivePretriggerShadowIntelligence", "PretriggerShadowCandidate",
    "PretriggerShadowEvaluation", "SHADOW_VERSION", "STANDARD_PATH",
    "EXCEPTIONAL_PATH",
]
