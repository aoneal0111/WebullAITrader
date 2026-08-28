"""Non-executable forward outcomes for rejected Warrior evaluations.

This module deliberately depends only on immutable capture contracts and
completed minute bars.  It has no order, trading-service, gateway, broker, or
authorization port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median
from typing import Mapping, Protocol

from app.live_scanner.session import scanner_session

from .forward_models import CaptureRecord, CaptureRecordType, PointInTimeObservation
from .forward_store import ForwardCaptureStore
from .models import MinuteBar, MomentumCandidate, SetupState


ONE_MINUTE = timedelta(minutes=1)
HUNDRED = Decimal("100")


class ShadowOutcomeStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE_MISSING_FUTURE_DATA = "INCOMPLETE_MISSING_FUTURE_DATA"
    INCOMPLETE_SESSION_BOUNDARY = "INCOMPLETE_SESSION_BOUNDARY"


class ShadowClassification(StrEnum):
    GOOD_REJECTION = "GOOD_REJECTION"
    NEUTRAL_REJECTION = "NEUTRAL_REJECTION"
    MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"
    MISSED_OPPORTUNITY_PRICE_MOVE_ONLY = "MISSED_OPPORTUNITY_PRICE_MOVE_ONLY"
    DANGEROUS_MISSED_OPPORTUNITY = "DANGEROUS_MISSED_OPPORTUNITY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ShadowAnalysisConfiguration:
    """Analysis knobs that cannot authorize or configure production trading."""

    horizons_minutes: tuple[int, ...] = (1, 2, 5, 10)
    reward_multiples: tuple[Decimal, ...] = (Decimal("1"), Decimal("2"))
    meaningful_price_move_percent: Decimal = Decimal("2")
    authority: str = "SHADOW"
    execution_capability: str = "NON_EXECUTABLE"
    purpose: str = "ANALYSIS_ONLY"

    def __post_init__(self) -> None:
        if (
            not self.horizons_minutes
            or any(value <= 0 for value in self.horizons_minutes)
            or tuple(sorted(set(self.horizons_minutes))) != self.horizons_minutes
            or not self.reward_multiples
            or any(value <= 0 for value in self.reward_multiples)
            or self.meaningful_price_move_percent <= 0
        ):
            raise ValueError("shadow analysis settings are invalid")
        if (
            self.authority != "SHADOW"
            or self.execution_capability != "NON_EXECUTABLE"
            or self.purpose != "ANALYSIS_ONLY"
        ):
            raise ValueError("shadow analysis authority markers are immutable")


class ShadowPolicy(Protocol):
    """Extension point for labeled counterfactuals, never executable signals."""

    @property
    def name(self) -> str: ...

    def evaluate(
        self, evaluation: Mapping[str, object],
        completed_bars: tuple[MinuteBar, ...],
    ) -> Mapping[str, object]: ...


@dataclass(slots=True)
class _TrackedEvaluation:
    record: CaptureRecord
    payload: dict[str, object]
    bars: dict[datetime, MinuteBar] = field(default_factory=dict)
    completed_horizons: set[int] = field(default_factory=set)

    @property
    def timestamp(self) -> datetime:
        return datetime.fromisoformat(str(self.payload["evaluation_timestamp"]))


class ShadowOpportunityAnalyzer:
    """Build deterministic observations without possessing an execution port."""

    def __init__(
        self,
        store: ForwardCaptureStore,
        config: ShadowAnalysisConfiguration = ShadowAnalysisConfiguration(),
    ) -> None:
        self.store = store
        self.config = config
        self._active: dict[str, _TrackedEvaluation] = {}
        self._by_symbol: dict[str, set[str]] = {}
        self._recover()

    def observe_rejection(
        self,
        decision: CaptureRecord,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        blocking_reason_codes: tuple[str, ...],
        *,
        scanner_rank: int | None = None,
        scanner_score: int | None = None,
        scanner_classification: str | None = None,
        scanner_failed_rules: tuple[str, ...] = (),
    ) -> CaptureRecord:
        setup = candidate.setup
        trigger = None if setup is None else setup.trigger
        stop = None if setup is None else setup.stop_price
        plan_valid = bool(
            setup is not None
            and setup.state is SetupState.TRIGGERED
            and trigger is not None
            and stop is not None
            and trigger > stop
        )
        payload: dict[str, object] = {
            "authority": self.config.authority,
            "execution_capability": self.config.execution_capability,
            "purpose": self.config.purpose,
            "decision_record_id": decision.record_id,
            "evaluation_timestamp": candidate.timestamp,
            "sampling_resolution": "COMPLETED_1_MINUTE_OHLC",
            "session": candidate.session,
            "scanner_rank": scanner_rank,
            "scanner_score": scanner_score,
            "scanner_classification": scanner_classification,
            "scanner_failed_rules": scanner_failed_rules,
            "warrior_rank": candidate.rank,
            "warrior_score": candidate.score.total,
            "warrior_status": candidate.status.value,
            "policy_version": candidate.policy_version,
            "discovery_status": "PASSED" if candidate.discovery_qualified else "BLOCKED",
            "entry_status": "BLOCKED",
            "setup_type": None if setup is None else setup.setup_type.value,
            "setup_state": None if setup is None else setup.state.value,
            "price": candidate.price,
            "bid": value.observation.bid,
            "ask": value.observation.ask,
            "spread_percent": candidate.spread_percent,
            "relative_volume": candidate.relative_volume,
            "volume": candidate.volume,
            "dollar_volume": candidate.dollar_volume,
            "float_shares": candidate.float_shares,
            "distance_from_hod_percent": candidate.distance_from_hod_percent,
            "catalyst_state": candidate.catalyst_status.value,
            "trigger": trigger,
            "stop": stop,
            "counterfactual_entry_valid": plan_valid,
            "reason_codes": tuple(dict.fromkeys(blocking_reason_codes)),
            "horizons_minutes": self.config.horizons_minutes,
            "reward_multiples": self.config.reward_multiples,
            "meaningful_price_move_percent": self.config.meaningful_price_move_percent,
        }
        record = CaptureRecord.create(
            CaptureRecordType.SHADOW_EVALUATION,
            candidate.symbol,
            candidate.timestamp,
            payload,
            identity_parts=(decision.record_id,),
        )
        if record.record_id not in self._active:
            tracked = _TrackedEvaluation(record, record.payload)
            self._active[record.record_id] = tracked
            self._by_symbol.setdefault(record.symbol, set()).add(record.record_id)
        return record

    def observe_bar(self, bar: MinuteBar) -> tuple[CaptureRecord, ...]:
        records: list[CaptureRecord] = []
        for evaluation_id in tuple(self._by_symbol.get(bar.symbol.strip().upper(), ())):
            tracked = self._active.get(evaluation_id)
            if tracked is None or bar.timestamp < _first_future_minute(tracked.timestamp):
                continue
            tracked.bars[bar.timestamp] = bar
            records.extend(self._resolve_due(tracked, bar.timestamp + ONE_MINUTE))
        return tuple(records)

    def finalize_due(self, observed_at: datetime) -> tuple[CaptureRecord, ...]:
        if observed_at.tzinfo is None:
            raise ValueError("shadow finalization timestamp must be timezone-aware")
        records: list[CaptureRecord] = []
        for tracked in tuple(self._active.values()):
            records.extend(self._resolve_due(tracked, observed_at, final=True))
        return tuple(records)

    def policy_record(
        self, evaluation_record_id: str, symbol: str, timestamp: datetime,
        policy: ShadowPolicy, *, completed_bars: tuple[MinuteBar, ...] = (),
    ) -> CaptureRecord:
        tracked = self._active.get(evaluation_record_id)
        if tracked is None:
            raise KeyError(evaluation_record_id)
        result = dict(policy.evaluate(tracked.payload, completed_bars))
        return CaptureRecord.create(
            CaptureRecordType.SHADOW_POLICY_RESULT, symbol, timestamp,
            {
                "authority": "SHADOW",
                "execution_capability": "NON_EXECUTABLE",
                "purpose": "ANALYSIS_ONLY",
                "policy_name": policy.name,
                "evaluation_record_id": evaluation_record_id,
                "result": result,
            },
            identity_parts=(evaluation_record_id, policy.name),
        )

    def _resolve_due(
        self, tracked: _TrackedEvaluation, observed_at: datetime, *, final: bool = False,
    ) -> list[CaptureRecord]:
        result: list[CaptureRecord] = []
        for horizon in self.config.horizons_minutes:
            if horizon in tracked.completed_horizons:
                continue
            target = tracked.timestamp + timedelta(minutes=horizon)
            if observed_at < target:
                continue
            record = self._outcome_record(tracked, horizon, observed_at, final=final)
            if record is None:
                continue
            tracked.completed_horizons.add(horizon)
            result.append(record)
        if len(tracked.completed_horizons) == len(self.config.horizons_minutes):
            self._remove(tracked.record.record_id)
        return result

    def _outcome_record(
        self, tracked: _TrackedEvaluation, horizon: int, observed_at: datetime,
        *, final: bool,
    ) -> CaptureRecord | None:
        target = tracked.timestamp + timedelta(minutes=horizon)
        if scanner_session(target).value != str(tracked.payload["session"]):
            return self._unavailable(
                tracked, horizon, observed_at,
                ShadowOutcomeStatus.INCOMPLETE_SESSION_BOUNDARY,
                "TARGET_CROSSES_SESSION_BOUNDARY",
            )
        ordered = sorted(tracked.bars.values(), key=lambda item: item.timestamp)
        sample = next(
            (item for item in ordered if item.timestamp + ONE_MINUTE >= target),
            None,
        )
        if sample is None:
            if not final:
                return None
            return self._unavailable(
                tracked, horizon, observed_at,
                ShadowOutcomeStatus.INCOMPLETE_MISSING_FUTURE_DATA,
                "NO_COMPLETED_BAR_AT_HORIZON",
            )
        if sample.timestamp + ONE_MINUTE > target + ONE_MINUTE:
            return self._unavailable(
                tracked, horizon, observed_at,
                ShadowOutcomeStatus.INCOMPLETE_MISSING_FUTURE_DATA,
                "SAMPLE_MORE_THAN_ONE_MINUTE_AFTER_TARGET",
            )
        path = [item for item in ordered if item.timestamp <= sample.timestamp]
        expected = _first_future_minute(tracked.timestamp)
        for item in path:
            if item.timestamp != expected:
                return self._unavailable(
                    tracked, horizon, observed_at,
                    ShadowOutcomeStatus.INCOMPLETE_MISSING_FUTURE_DATA,
                    "NONCONTIGUOUS_MINUTE_BARS",
                )
            expected += ONE_MINUTE
        if not path:
            return None
        return _complete_outcome(tracked, horizon, target, sample, tuple(path), self.config)

    def _unavailable(
        self, tracked: _TrackedEvaluation, horizon: int, observed_at: datetime,
        status: ShadowOutcomeStatus, reason: str,
    ) -> CaptureRecord:
        payload = {
            "authority": "SHADOW", "execution_capability": "NON_EXECUTABLE",
            "purpose": "ANALYSIS_ONLY",
            "evaluation_record_id": tracked.record.record_id,
            "decision_record_id": tracked.payload["decision_record_id"],
            "horizon_minutes": horizon,
            "target_timestamp": tracked.timestamp + timedelta(minutes=horizon),
            "status": status.value, "unavailable_reason": reason,
            "sampling_resolution": "COMPLETED_1_MINUTE_OHLC",
            "classification": ShadowClassification.UNAVAILABLE.value,
            "reason_codes": tracked.payload["reason_codes"],
        }
        return CaptureRecord.create(
            CaptureRecordType.SHADOW_OUTCOME, tracked.record.symbol, observed_at,
            payload, identity_parts=(tracked.record.record_id, str(horizon)),
        )

    def _recover(self) -> None:
        completed: dict[str, set[int]] = {}
        for record in self.store.records(record_type=CaptureRecordType.SHADOW_OUTCOME):
            payload = record.payload
            evaluation_id = str(payload.get("evaluation_record_id", ""))
            if evaluation_id:
                completed.setdefault(evaluation_id, set()).add(int(payload["horizon_minutes"]))
        for record in self.store.records(record_type=CaptureRecordType.SHADOW_EVALUATION):
            done = completed.get(record.record_id, set())
            if len(done) == len(self.config.horizons_minutes):
                continue
            tracked = _TrackedEvaluation(record, record.payload, completed_horizons=set(done))
            self._active[record.record_id] = tracked
            self._by_symbol.setdefault(record.symbol, set()).add(record.record_id)
        for record in self.store.records(record_type=CaptureRecordType.MINUTE_BAR):
            payload = record.payload
            try:
                bar = MinuteBar(
                    record.symbol, datetime.fromisoformat(str(payload["bar_timestamp"])),
                    Decimal(str(payload["open"])), Decimal(str(payload["high"])),
                    Decimal(str(payload["low"])), Decimal(str(payload["close"])),
                    Decimal(str(payload["volume"])),
                )
            except (KeyError, ValueError):
                continue
            for evaluation_id in self._by_symbol.get(record.symbol, ()):
                tracked = self._active[evaluation_id]
                if bar.timestamp >= _first_future_minute(tracked.timestamp):
                    tracked.bars[bar.timestamp] = bar

    def _remove(self, evaluation_id: str) -> None:
        tracked = self._active.pop(evaluation_id, None)
        if tracked is None:
            return
        identifiers = self._by_symbol.get(tracked.record.symbol)
        if identifiers is not None:
            identifiers.discard(evaluation_id)
            if not identifiers:
                self._by_symbol.pop(tracked.record.symbol, None)


def _complete_outcome(
    tracked: _TrackedEvaluation, horizon: int, target: datetime,
    sample: MinuteBar, path: tuple[MinuteBar, ...], config: ShadowAnalysisConfiguration,
) -> CaptureRecord:
    price = Decimal(str(tracked.payload["price"]))
    highest_bar = max(path, key=lambda item: item.high)
    lowest_bar = min(path, key=lambda item: item.low)
    highest = highest_bar.high
    lowest = lowest_bar.low
    mfe_percent = (highest - price) / price * HUNDRED
    mae_percent = (lowest - price) / price * HUNDRED
    plan = _hypothetical_plan(tracked.payload, path, config.reward_multiples)
    classification = _classification(
        sample.close, price, mfe_percent, plan, config.meaningful_price_move_percent,
    )
    payload = {
        "authority": "SHADOW", "execution_capability": "NON_EXECUTABLE",
        "purpose": "ANALYSIS_ONLY",
        "evaluation_record_id": tracked.record.record_id,
        "decision_record_id": tracked.payload["decision_record_id"],
        "horizon_minutes": horizon, "target_timestamp": target,
        "status": ShadowOutcomeStatus.COMPLETE.value,
        "sampling_resolution": "COMPLETED_1_MINUTE_OHLC",
        "sample_bar_timestamp": sample.timestamp,
        "sample_completion_timestamp": sample.timestamp + ONE_MINUTE,
        "sample_offset_seconds": Decimal(str((sample.timestamp + ONE_MINUTE - target).total_seconds())),
        "evaluation_price": price, "subsequent_price": sample.close,
        "return_percent": (sample.close - price) / price * HUNDRED,
        "highest_observed_price": highest, "lowest_observed_price": lowest,
        "mfe_percent": mfe_percent, "mae_percent": mae_percent,
        "mfe_source_bar_timestamp": highest_bar.timestamp,
        "mae_source_bar_timestamp": lowest_bar.timestamp,
        "time_to_mfe_seconds_approx": Decimal(str((highest_bar.timestamp + ONE_MINUTE - tracked.timestamp).total_seconds())),
        "time_to_mae_seconds_approx": Decimal(str((lowest_bar.timestamp + ONE_MINUTE - tracked.timestamp).total_seconds())),
        "classification": classification.value,
        "reason_codes": tracked.payload["reason_codes"],
        "hypothetical_trade": plan,
    }
    return CaptureRecord.create(
        CaptureRecordType.SHADOW_OUTCOME, tracked.record.symbol,
        sample.timestamp + ONE_MINUTE, payload,
        identity_parts=(tracked.record.record_id, str(horizon)),
    )


def _hypothetical_plan(
    evaluation: Mapping[str, object], path: tuple[MinuteBar, ...],
    rewards: tuple[Decimal, ...],
) -> dict[str, object]:
    if not bool(evaluation.get("counterfactual_entry_valid")):
        return {
            "applicable": False,
            "state": "NOT_APPLICABLE_NO_AUTHORITATIVE_TRIGGERED_PLAN",
            "trigger": evaluation.get("trigger"), "stop": evaluation.get("stop"),
            "reward_hits": (),
        }
    trigger = Decimal(str(evaluation["trigger"]))
    stop = Decimal(str(evaluation["stop"]))
    risk = trigger - stop
    triggered_at = None
    stop_at = None
    reward_hits: dict[Decimal, datetime] = {}
    trade_high = trigger
    trade_low = trigger
    for bar in path:
        if triggered_at is None:
            if bar.high < trigger:
                continue
            triggered_at = bar.timestamp
        trade_high = max(trade_high, bar.high)
        trade_low = min(trade_low, bar.low)
        # Completed minute bars cannot establish intrabar order.  The
        # conservative analytical convention treats a stop as first when a
        # stop and target coexist in the same bar.
        if stop_at is None and bar.low <= stop:
            stop_at = bar.timestamp
            continue
        if stop_at is None:
            for reward in rewards:
                if reward not in reward_hits and bar.high >= trigger + risk * reward:
                    reward_hits[reward] = bar.timestamp
    if triggered_at is None:
        state = "NEVER_TRIGGERED"
    elif stop_at is not None:
        state = "HIT_STOP"
    elif reward_hits:
        state = "REACHED_REWARD"
    else:
        state = "TRIGGERED_UNRESOLVED"
    return {
        "applicable": True, "state": state, "trigger": trigger, "stop": stop,
        "risk_per_share": risk, "triggered_at_bar": triggered_at,
        "stop_hit_at_bar": stop_at,
        "reward_hits": tuple(
            {"multiple": reward, "bar_timestamp": timestamp}
            for reward, timestamp in sorted(reward_hits.items())
        ),
        "mfe_r": (trade_high - trigger) / risk if triggered_at is not None else None,
        "mae_r": (trade_low - trigger) / risk if triggered_at is not None else None,
        "same_bar_conflict_policy": "STOP_FIRST_CONSERVATIVE_1M_OHLC",
    }


def _classification(
    subsequent: Decimal, evaluation_price: Decimal, mfe_percent: Decimal,
    plan: Mapping[str, object], meaningful_percent: Decimal,
) -> ShadowClassification:
    reward_hits = tuple(plan.get("reward_hits", ()))
    hit_one_r = any(Decimal(str(item["multiple"])) >= 1 for item in reward_hits)
    if bool(plan.get("applicable")):
        stop_at = plan.get("stop_hit_at_bar")
        if stop_at is not None and Decimal(str(plan.get("mfe_r", "0"))) >= 1 and not hit_one_r:
            return ShadowClassification.DANGEROUS_MISSED_OPPORTUNITY
        if hit_one_r:
            return ShadowClassification.MISSED_OPPORTUNITY
    if mfe_percent >= meaningful_percent:
        return ShadowClassification.MISSED_OPPORTUNITY_PRICE_MOVE_ONLY
    if subsequent < evaluation_price:
        return ShadowClassification.GOOD_REJECTION
    return ShadowClassification.NEUTRAL_REJECTION


def _first_future_minute(timestamp: datetime) -> datetime:
    minute = timestamp.replace(second=0, microsecond=0)
    return minute if timestamp == minute else minute + ONE_MINUTE


def build_rejection_attribution(store: ForwardCaptureStore) -> dict[str, dict[str, object]]:
    """Aggregate complete outcomes by every blocker and exact blocker set."""
    evaluations = {
        record.record_id: record.payload
        for record in store.records(record_type=CaptureRecordType.SHADOW_EVALUATION)
    }
    outcomes_by_evaluation: dict[str, list[dict[str, object]]] = {}
    for record in store.records(record_type=CaptureRecordType.SHADOW_OUTCOME):
        payload = record.payload
        outcomes_by_evaluation.setdefault(
            str(payload.get("evaluation_record_id", "")), [],
        ).append(payload)
    groups: dict[str, dict[str, object]] = {}
    for evaluation_id, evaluation in evaluations.items():
        reasons = tuple(str(value) for value in evaluation.get("reason_codes", ()))
        keys = (*reasons, " + ".join(reasons)) if reasons else ("UNATTRIBUTED",)
        related = outcomes_by_evaluation.get(evaluation_id, [])
        for key in dict.fromkeys(keys):
            group = groups.setdefault(key, {
                "candidates_rejected": 0, "candidates_with_complete_forward_data": 0,
                "meaningful_favorable_moves": 0, "returns_by_horizon": {},
                "mfe_percent_by_horizon": {}, "mae_percent_by_horizon": {},
            })
            group["candidates_rejected"] = int(group["candidates_rejected"]) + 1
            complete = [item for item in related if item.get("status") == "COMPLETE"]
            if len(complete) == len(evaluation.get("horizons_minutes", ())):
                group["candidates_with_complete_forward_data"] = int(group["candidates_with_complete_forward_data"]) + 1
            if any("MISSED_OPPORTUNITY" in str(item.get("classification")) for item in complete):
                group["meaningful_favorable_moves"] = int(group["meaningful_favorable_moves"]) + 1
            by_horizon = group["returns_by_horizon"]
            assert isinstance(by_horizon, dict)
            for item in complete:
                horizon = int(item["horizon_minutes"])
                by_horizon.setdefault(horizon, []).append(Decimal(str(item["return_percent"])))
                group["mfe_percent_by_horizon"].setdefault(horizon, []).append(
                    Decimal(str(item["mfe_percent"]))
                )
                group["mae_percent_by_horizon"].setdefault(horizon, []).append(
                    Decimal(str(item["mae_percent"]))
                )
    for group in groups.values():
        by_horizon = group["returns_by_horizon"]
        group["median_return_by_horizon"] = {
            horizon: median(values) for horizon, values in sorted(by_horizon.items())
        }
    return groups


__all__ = [
    "ShadowAnalysisConfiguration", "ShadowClassification", "ShadowOutcomeStatus",
    "ShadowOpportunityAnalyzer", "ShadowPolicy", "build_rejection_attribution",
]
