"""Bounded, replayable, research-only crypto outcome learning."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping, Sequence

from app.research_core import AssetResearchIdentity, EvidenceProvenance, PerUnitRiskGeometry, semantic_digest

from .evidence import CryptoCompletedBar, CryptoRegimeLabel
from .strategies import CryptoStrategyDetection


SUPPORTED_HORIZONS_SECONDS = (60, 300, 900, 1800)
UNSUPPORTED_HORIZONS_SECONDS = (5, 15, 30)
MAX_ACTIVE_EPISODES = 8192
MAX_DEDUP_IDENTITIES = 16384
SCHEMA_VERSION = "crypto-outcome-v1"
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class CryptoOutcomeStatus(StrEnum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    INSUFFICIENT_PATH_DATA = "INSUFFICIENT_PATH_DATA"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    EXPIRED = "EXPIRED"
    CANCELLED_RESEARCH_EPISODE = "CANCELLED_RESEARCH_EPISODE"


@dataclass(frozen=True, slots=True)
class CryptoOutcomeDecision:
    identity: AssetResearchIdentity
    canonical_symbol: str
    strategy_id: str
    strategy_version: str
    decision_timestamp: datetime
    decision_cutoff: datetime
    reference_price: Decimal
    structural_stop: Decimal
    risk_per_unit: Decimal
    regime_at_decision: CryptoRegimeLabel
    regime_window_at_decision: str
    btc_relative_strength_at_decision: Decimal | None
    eth_relative_strength_at_decision: Decimal | None
    btc_correlation_at_decision: Decimal | None
    eth_correlation_at_decision: Decimal | None
    breadth_at_decision: Decimal | None
    dispersion_at_decision: Decimal | None
    strategy_memberships: tuple[str, ...]
    cohort_identity: str
    provenance: EvidenceProvenance
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only or self.identity.asset_type.value != "CRYPTO":
            raise ValueError("crypto outcome decisions must remain research-only")
        if self.reference_price <= 0 or self.structural_stop <= 0 or self.risk_per_unit <= 0:
            raise ValueError("outcome geometry must be positive")
        if self.reference_price <= self.structural_stop:
            raise ValueError("long crypto outcome requires entry above stop")
        if self.risk_per_unit != self.reference_price - self.structural_stop:
            raise ValueError("risk_per_unit must equal entry-to-stop distance")
        if not self.strategy_memberships or len(set(self.strategy_memberships)) != len(self.strategy_memberships):
            raise ValueError("strategy memberships must be unique")
        if self.provenance.decision_cutoff != self.decision_cutoff:
            raise ValueError("decision provenance cutoff mismatch")

    @classmethod
    def from_detection(
        cls,
        detection: CryptoStrategyDetection,
        *,
        decision_timestamp: datetime,
        regime_window: str,
        btc_relative_strength: Decimal | None,
        eth_relative_strength: Decimal | None,
        btc_correlation: Decimal | None,
        eth_correlation: Decimal | None,
        breadth: Decimal | None,
        dispersion: Decimal | None,
        memberships: tuple[str, ...] | None = None,
    ) -> "CryptoOutcomeDecision":
        geometry = detection.risk_geometry
        if geometry is None:
            raise ValueError("detection has no research geometry")
        identity = detection.identity
        cohort = semantic_digest(identity.canonical_symbol, memberships or (detection.strategy_id,), regime_window)
        return cls(
            identity, identity.canonical_symbol, detection.strategy_id,
            detection.definition.version, decision_timestamp, identity.decision_cutoff,
            geometry.entry_reference, geometry.structural_stop, geometry.risk_per_unit,
            CryptoRegimeLabel.MIXED if not regime_window else CryptoRegimeLabel(regime_window) if regime_window in {x.value for x in CryptoRegimeLabel} else CryptoRegimeLabel.INSUFFICIENT_EVIDENCE,
            regime_window, btc_relative_strength, eth_relative_strength,
            btc_correlation, eth_correlation, breadth, dispersion,
            tuple(sorted(set(memberships or (detection.strategy_id,)))), cohort,
            detection.detection.provenance,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "asset_type": "CRYPTO",
            "canonical_symbol": self.canonical_symbol,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "opportunity_id": self.identity.opportunity_id,
            "lifecycle_id": self.identity.lifecycle_id,
            "decision_timestamp": self.decision_timestamp.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "reference_price": str(self.reference_price),
            "structural_stop": str(self.structural_stop),
            "risk_per_unit": str(self.risk_per_unit),
            "regime_at_decision": self.regime_at_decision.value,
            "regime_window_at_decision": self.regime_window_at_decision,
            "btc_relative_strength_at_decision": _decimal(self.btc_relative_strength_at_decision),
            "eth_relative_strength_at_decision": _decimal(self.eth_relative_strength_at_decision),
            "btc_correlation_at_decision": _decimal(self.btc_correlation_at_decision),
            "eth_correlation_at_decision": _decimal(self.eth_correlation_at_decision),
            "breadth_at_decision": _decimal(self.breadth_at_decision),
            "dispersion_at_decision": _decimal(self.dispersion_at_decision),
            "strategy_memberships": list(self.strategy_memberships),
            "cohort_identity": self.cohort_identity,
            "research_only": True,
            "production_promoted": False,
            "selection_authorized": False,
            "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class CryptoResearchOutcome:
    decision: CryptoOutcomeDecision
    horizon_seconds: int
    horizon_timestamp: datetime
    status: CryptoOutcomeStatus
    forward_return_percent: Decimal | None = None
    btc_benchmark_return_percent: Decimal | None = None
    eth_benchmark_return_percent: Decimal | None = None
    btc_relative_forward_return_percent: Decimal | None = None
    eth_relative_forward_return_percent: Decimal | None = None
    mae: Decimal | None = None
    mfe: Decimal | None = None
    mae_r: Decimal | None = None
    mfe_r: Decimal | None = None
    stop_reached: bool | None = None
    one_r_reached: bool | None = None
    two_r_reached: bool | None = None
    three_r_reached: bool | None = None
    time_to_stop: int | None = None
    time_to_1r: int | None = None
    time_to_2r: int | None = None
    time_to_3r: int | None = None
    first_event: str | None = None
    outcome_id: str = ""
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only or self.decision.research_only is not True:
            raise ValueError("crypto outcomes must remain research-only")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon must be positive")
        for value in (self.forward_return_percent, self.btc_benchmark_return_percent, self.eth_benchmark_return_percent, self.mae, self.mfe, self.mae_r, self.mfe_r):
            if value is not None and not value.is_finite():
                raise ValueError("outcome values must be finite")
        if not self.outcome_id:
            payload = (self.decision.identity.deterministic_id, self.horizon_seconds, self.status.value, self.forward_return_percent, self.mae, self.mfe, self.first_event)
            object.__setattr__(self, "outcome_id", semantic_digest(*payload))

    def to_dict(self) -> dict[str, object]:
        data = self.decision.to_dict()
        data.update({
            "outcome_id": self.outcome_id,
            "horizon_seconds": self.horizon_seconds,
            "horizon_timestamp": self.horizon_timestamp.isoformat(),
            "status": self.status.value,
            "forward_return_percent": _decimal(self.forward_return_percent),
            "btc_benchmark_return_percent": _decimal(self.btc_benchmark_return_percent),
            "eth_benchmark_return_percent": _decimal(self.eth_benchmark_return_percent),
            "btc_relative_forward_return_percent": _decimal(self.btc_relative_forward_return_percent),
            "eth_relative_forward_return_percent": _decimal(self.eth_relative_forward_return_percent),
            "mae": _decimal(self.mae), "mfe": _decimal(self.mfe),
            "mae_r": _decimal(self.mae_r), "mfe_r": _decimal(self.mfe_r),
            "stop_reached": self.stop_reached, "one_r_reached": self.one_r_reached,
            "two_r_reached": self.two_r_reached, "three_r_reached": self.three_r_reached,
            "time_to_stop": self.time_to_stop, "time_to_1r": self.time_to_1r,
            "time_to_2r": self.time_to_2r, "time_to_3r": self.time_to_3r,
            "first_event": self.first_event,
        })
        return data


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _aligned_return(bars: Sequence[CryptoCompletedBar], cutoff: datetime, horizon: datetime) -> Decimal | None:
    eligible = tuple(sorted((bar for bar in bars if cutoff < bar.closed_at <= horizon), key=lambda item: item.closed_at))
    if not eligible:
        return None
    return eligible[-1].close / eligible[0].open - ONE


def label_crypto_outcome(
    decision: CryptoOutcomeDecision,
    bars: Sequence[CryptoCompletedBar],
    *,
    horizon_seconds: int,
    btc_bars: Sequence[CryptoCompletedBar] = (),
    eth_bars: Sequence[CryptoCompletedBar] = (),
) -> CryptoResearchOutcome:
    """Label one immutable horizon using only bars closed by that horizon."""

    horizon = decision.decision_timestamp + timedelta(seconds=horizon_seconds)
    if horizon_seconds not in SUPPORTED_HORIZONS_SECONDS:
        return CryptoResearchOutcome(decision, horizon_seconds, horizon, CryptoOutcomeStatus.INSUFFICIENT_PATH_DATA)
    if decision.risk_per_unit <= 0 or decision.reference_price <= decision.structural_stop:
        return CryptoResearchOutcome(decision, horizon_seconds, horizon, CryptoOutcomeStatus.INVALID_GEOMETRY)
    path = tuple(sorted((bar for bar in bars if decision.decision_cutoff < bar.closed_at <= horizon), key=lambda item: item.closed_at))
    if not path:
        return CryptoResearchOutcome(decision, horizon_seconds, horizon, CryptoOutcomeStatus.INSUFFICIENT_PATH_DATA)
    last = path[-1]
    highest = max(item.high for item in path)
    lowest = min(item.low for item in path)
    mae = lowest - decision.reference_price
    mfe = highest - decision.reference_price
    stop = decision.structural_stop
    targets = tuple(decision.reference_price + decision.risk_per_unit * Decimal(index) for index in (1, 2, 3))
    reached = [None, None, None]
    stop_time = None
    first_event = None
    for bar in path:
        elapsed = int((bar.closed_at - decision.decision_timestamp).total_seconds())
        # OHLC cannot prove intrabar order; conservative rule is stop first.
        if bar.low <= stop and bar.high >= decision.reference_price:
            stop_time, first_event = elapsed, "STOP"
            break
        if bar.low <= stop:
            stop_time = elapsed
            first_event = first_event or "STOP"
            break
        for index, target in enumerate(targets):
            if reached[index] is None and bar.high >= target:
                reached[index] = elapsed
                first_event = first_event or f"{index + 1}R"
    forward = (last.close - decision.reference_price) / decision.reference_price * HUNDRED
    btc_return = _aligned_return(btc_bars, decision.decision_cutoff, horizon)
    eth_return = _aligned_return(eth_bars, decision.decision_cutoff, horizon)
    return CryptoResearchOutcome(
        decision, horizon_seconds, horizon, CryptoOutcomeStatus.COMPLETE,
        forward, None if btc_return is None else btc_return * HUNDRED,
        None if eth_return is None else eth_return * HUNDRED,
        None if btc_return is None else forward - btc_return * HUNDRED,
        None if eth_return is None else forward - eth_return * HUNDRED,
        mae, mfe, mae / decision.risk_per_unit, mfe / decision.risk_per_unit,
        stop_time is not None, reached[0] is not None, reached[1] is not None, reached[2] is not None,
        stop_time, reached[0], reached[1], reached[2], first_event,
    )


@dataclass(frozen=True, slots=True)
class CryptoOutcomeMetrics:
    crypto_outcomes_active_episodes: int
    crypto_outcomes_pending_labels: int
    crypto_outcomes_completed_labels: int
    crypto_outcomes_persisted: int
    crypto_outcomes_duplicates_suppressed: int
    crypto_outcomes_failures: int
    crypto_outcomes_queue_depth: int
    crypto_outcomes_queue_high_water: int
    crypto_outcomes_retained: int = 0
    crypto_outcomes_retained_high_water: int = 0
    crypto_outcomes_evicted: int = 0
    crypto_outcomes_dedup_identities: int = 0
    crypto_outcomes_dedup_high_water: int = 0


class CryptoOutcomeTracker:
    def __init__(self, *, maximum_active_episodes: int = MAX_ACTIVE_EPISODES, maximum_retained_outcomes: int = MAX_DEDUP_IDENTITIES, maximum_dedup_identities: int = MAX_DEDUP_IDENTITIES) -> None:
        if maximum_active_episodes <= 0 or maximum_retained_outcomes <= 0 or maximum_dedup_identities <= 0:
            raise ValueError("outcome bounds must be positive")
        self.maximum_active_episodes = maximum_active_episodes
        self.maximum_retained_outcomes = maximum_retained_outcomes
        self.maximum_dedup_identities = maximum_dedup_identities
        self._decisions: OrderedDict[str, CryptoOutcomeDecision] = OrderedDict()
        self._labeled_horizons: dict[str, set[int]] = {}
        self._outcomes: OrderedDict[str, CryptoResearchOutcome] = OrderedDict()
        self._dedup: OrderedDict[str, None] = OrderedDict()
        self._persisted = 0
        self._completed_total = 0
        self._duplicates = 0
        self._failures = 0
        self._active_high_water = 0
        self._retained_high_water = 0
        self._evicted = 0
        self._dedup_high_water = 0

    def register(self, decision: CryptoOutcomeDecision) -> bool:
        decision_id = decision.identity.deterministic_id
        if decision_id in self._decisions or any(key.startswith(decision_id + ":") for key in self._dedup):
            self._duplicates += 1
            return False
        if len(self._decisions) >= self.maximum_active_episodes:
            return False
        self._decisions[decision_id] = decision
        self._labeled_horizons[decision_id] = set()
        self._active_high_water = max(self._active_high_water, len(self._decisions))
        return True

    def update(self, decision_id: str, bars: Sequence[CryptoCompletedBar], *, btc_bars: Sequence[CryptoCompletedBar] = (), eth_bars: Sequence[CryptoCompletedBar] = ()) -> tuple[CryptoResearchOutcome, ...]:
        decision = self._decisions.get(decision_id)
        if decision is None:
            if any(key.startswith(decision_id + ":") for key in self._dedup):
                self._duplicates += 1
                return ()
            self._failures += 1
            return ()
        emitted = []
        for horizon in SUPPORTED_HORIZONS_SECONDS + UNSUPPORTED_HORIZONS_SECONDS:
            key = f"{decision_id}:{horizon}"
            if key in self._dedup:
                self._duplicates += 1
                continue
            if horizon in SUPPORTED_HORIZONS_SECONDS:
                horizon_end = decision.decision_timestamp + timedelta(seconds=horizon)
                latest_closed = max((bar.closed_at for bar in bars), default=None)
                if latest_closed is None or latest_closed < horizon_end:
                    continue
            try:
                outcome = label_crypto_outcome(decision, bars, horizon_seconds=horizon, btc_bars=btc_bars, eth_bars=eth_bars)
            except Exception:
                self._failures += 1
                continue
            self._outcomes[key] = outcome
            if outcome.status is CryptoOutcomeStatus.COMPLETE:
                self._completed_total += 1
            self._dedup[key] = None
            self._labeled_horizons.setdefault(decision_id, set()).add(horizon)
            self._outcomes.move_to_end(key)
            self._dedup.move_to_end(key)
            while len(self._outcomes) > self.maximum_retained_outcomes:
                self._outcomes.popitem(last=False)
                self._evicted += 1
            while len(self._dedup) > self.maximum_dedup_identities:
                self._dedup.popitem(last=False)
            self._retained_high_water = max(self._retained_high_water, len(self._outcomes))
            self._dedup_high_water = max(self._dedup_high_water, len(self._dedup))
            emitted.append(outcome)
        if all(horizon in self._labeled_horizons.get(decision_id, set()) for horizon in SUPPORTED_HORIZONS_SECONDS):
            self._decisions.pop(decision_id, None)
            self._labeled_horizons.pop(decision_id, None)
        return tuple(emitted)

    def record_persisted(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("persisted count cannot be negative")
        self._persisted += count

    def metrics(self) -> CryptoOutcomeMetrics:
        return CryptoOutcomeMetrics(
            len(self._decisions), len(self._decisions) * len(SUPPORTED_HORIZONS_SECONDS),
            self._completed_total, self._persisted, self._duplicates, self._failures, 0,
            self._active_high_water, len(self._outcomes), self._retained_high_water,
            self._evicted, len(self._dedup), self._dedup_high_water,
        )


class CryptoOutcomeJsonlStore:
    """Optional external append-only research store; disabled when path is None."""
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self.persisted = 0

    def append(self, outcome: CryptoResearchOutcome) -> bool:
        if self.path is None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        self.persisted += 1
        return True


@dataclass(frozen=True, slots=True)
class CryptoOutcomeSummary:
    cohort: tuple[str, ...]
    sample_count: int
    confidence: str
    mean_return_percent: Decimal | None
    median_return_percent: Decimal | None
    positive_return_rate: Decimal | None
    mean_mae: Decimal | None
    mean_mfe: Decimal | None
    mean_mae_r: Decimal | None
    mean_mfe_r: Decimal | None
    one_r_rate: Decimal | None
    two_r_rate: Decimal | None
    three_r_rate: Decimal | None
    stop_first_rate: Decimal | None


def summarize_crypto_outcomes(outcomes: Iterable[CryptoResearchOutcome]) -> tuple[CryptoOutcomeSummary, ...]:
    groups: dict[tuple[str, ...], list[CryptoResearchOutcome]] = {}
    for item in outcomes:
        if item.status is not CryptoOutcomeStatus.COMPLETE:
            continue
        key = (item.decision.strategy_id, str(item.horizon_seconds), item.decision.regime_at_decision.value, item.decision.cohort_identity)
        groups.setdefault(key, []).append(item)
    result = []
    for key, values in sorted(groups.items()):
        def avg(name: str) -> Decimal | None:
            nums = [getattr(item, name) for item in values if getattr(item, name) is not None]
            return None if not nums else sum(nums, ZERO) / Decimal(len(nums))
        def rate(name: str) -> Decimal | None:
            nums = [getattr(item, name) for item in values if getattr(item, name) is not None]
            return None if not nums else Decimal(sum(bool(x) for x in nums)) / Decimal(len(nums)) * HUNDRED
        count = len(values)
        result.append(CryptoOutcomeSummary(key, count, "INSUFFICIENT_SAMPLE" if count < 11 else "EARLY_SAMPLE" if count < 30 else "RESEARCH_SAMPLE", avg("forward_return_percent"), median([item.forward_return_percent for item in values]), rate("forward_return_percent"), avg("mae"), avg("mfe"), avg("mae_r"), avg("mfe_r"), rate("one_r_reached"), rate("two_r_reached"), rate("three_r_reached"), rate("stop_reached")))
    return tuple(result)


def replay_crypto_outcomes(decision: CryptoOutcomeDecision, bars: Sequence[CryptoCompletedBar], *, btc_bars: Sequence[CryptoCompletedBar] = (), eth_bars: Sequence[CryptoCompletedBar] = ()) -> tuple[CryptoResearchOutcome, ...]:
    return tuple(label_crypto_outcome(decision, bars, horizon_seconds=horizon, btc_bars=btc_bars, eth_bars=eth_bars) for horizon in SUPPORTED_HORIZONS_SECONDS + UNSUPPORTED_HORIZONS_SECONDS)


__all__ = [
    "CryptoOutcomeDecision", "CryptoOutcomeJsonlStore", "CryptoOutcomeMetrics",
    "CryptoOutcomeStatus", "CryptoOutcomeTracker", "CryptoResearchOutcome",
    "CryptoOutcomeSummary", "MAX_ACTIVE_EPISODES", "MAX_DEDUP_IDENTITIES",
    "SCHEMA_VERSION", "SUPPORTED_HORIZONS_SECONDS", "UNSUPPORTED_HORIZONS_SECONDS",
    "label_crypto_outcome", "replay_crypto_outcomes", "summarize_crypto_outcomes",
]
