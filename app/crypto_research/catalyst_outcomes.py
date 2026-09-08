"""Offline, descriptive linkage of catalyst snapshots to crypto outcomes.

Phase G2 deliberately consumes already-frozen Phase C/Phase D/Phase G records.  It
does not query providers, recompute indicators, fit models, or grant execution
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from statistics import median
from typing import Iterable

from app.research_core import semantic_digest

from .catalyst_snapshot import (
    CryptoCatalystDecisionSnapshot,
    CryptoCatalystOverallState,
    CryptoCatalystProviderAvailability,
    CryptoCatalystQualityState,
)
from .outcomes import (
    SUPPORTED_HORIZONS_SECONDS,
    CryptoOutcomeDecision,
    CryptoOutcomeStatus,
    CryptoResearchOutcome,
)


SCHEMA_VERSION = "crypto-catalyst-outcomes-v1"
MAX_OBSERVATIONS = 16384
MAX_COHORT_KEYS = 4096
MAX_MEMBERSHIPS_PER_OBSERVATION = 128
ZERO = Decimal("0")
HUNDRED = Decimal("100")


class CryptoCatalystCohortState(StrEnum):
    QUALIFIED_CATALYST = "QUALIFIED_CATALYST"
    NO_QUALIFIED_CATALYST = "NO_QUALIFIED_CATALYST"
    LIMITED_CATALYST_EVIDENCE = "LIMITED_CATALYST_EVIDENCE"
    STALE_CATALYST_ONLY = "STALE_CATALYST_ONLY"
    AMBIGUOUS_CATALYST_EVIDENCE = "AMBIGUOUS_CATALYST_EVIDENCE"
    UNRESOLVED_CATALYST_EVIDENCE = "UNRESOLVED_CATALYST_EVIDENCE"
    CONFLICTING_CATALYST_EVIDENCE = "CONFLICTING_CATALYST_EVIDENCE"
    NO_CATALYST_EVIDENCE = "NO_CATALYST_EVIDENCE"
    PROVIDER_AVAILABILITY_LIMITED = "PROVIDER_AVAILABILITY_LIMITED"


class CryptoCatalystSampleState(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    EARLY = "EARLY"
    RESEARCH = "RESEARCH"


def _sample_state(count: int) -> CryptoCatalystSampleState:
    if count < 11:
        return CryptoCatalystSampleState.INSUFFICIENT
    if count < 30:
        return CryptoCatalystSampleState.EARLY
    return CryptoCatalystSampleState.RESEARCH


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _avg(values: Iterable[Decimal | None]) -> Decimal | None:
    items = tuple(value for value in values if value is not None)
    return None if not items else sum(items, ZERO) / Decimal(len(items))


def _med(values: Iterable[Decimal | None]) -> Decimal | None:
    items = tuple(value for value in values if value is not None)
    return None if not items else Decimal(str(median(items)))


def _rate(values: Iterable[bool | None]) -> Decimal | None:
    items = tuple(value for value in values if value is not None)
    return None if not items else Decimal(sum(bool(value) for value in items)) / Decimal(len(items)) * HUNDRED


def _availability_limited(snapshot: CryptoCatalystDecisionSnapshot) -> bool:
    return any(item.availability_state in {CryptoCatalystProviderAvailability.BLOCKED, CryptoCatalystProviderAvailability.FAILED} for item in snapshot.provider_availability)


def _cohort_state(snapshot: CryptoCatalystDecisionSnapshot) -> CryptoCatalystCohortState:
    if _availability_limited(snapshot) and snapshot.summary.overall_evidence_state in {CryptoCatalystOverallState.NO_EVIDENCE, CryptoCatalystOverallState.UNAVAILABLE}:
        return CryptoCatalystCohortState.PROVIDER_AVAILABILITY_LIMITED
    state = snapshot.summary.overall_evidence_state
    if state is CryptoCatalystOverallState.NO_EVIDENCE:
        return CryptoCatalystCohortState.NO_CATALYST_EVIDENCE
    if state is CryptoCatalystOverallState.UNAVAILABLE:
        return CryptoCatalystCohortState.PROVIDER_AVAILABILITY_LIMITED
    if state is CryptoCatalystOverallState.CONFLICTING_EVIDENCE:
        return CryptoCatalystCohortState.CONFLICTING_CATALYST_EVIDENCE
    if state is CryptoCatalystOverallState.AMBIGUOUS_EVIDENCE:
        return CryptoCatalystCohortState.AMBIGUOUS_CATALYST_EVIDENCE
    if state is CryptoCatalystOverallState.STALE_ONLY:
        return CryptoCatalystCohortState.STALE_CATALYST_ONLY
    if state is CryptoCatalystOverallState.LIMITED_EVIDENCE:
        return CryptoCatalystCohortState.LIMITED_CATALYST_EVIDENCE
    if snapshot.summary.qualified_event_count:
        return CryptoCatalystCohortState.QUALIFIED_CATALYST
    return CryptoCatalystCohortState.NO_QUALIFIED_CATALYST


@dataclass(frozen=True, slots=True)
class CryptoCatalystOutcomeObservation:
    """One immutable strategy decision x outcome horizon research observation."""

    schema_version: str
    observation_identity: str
    canonical_pair: str
    decision_cutoff: datetime
    strategy_id: str
    strategy_version: str
    opportunity_identity: str
    detection_identity: str
    strategy_overlap_ids: tuple[str, ...]
    catalyst_snapshot_identity: str
    catalyst_snapshot_schema_version: str
    catalyst_state: CryptoCatalystCohortState
    catalyst_overall_evidence_state: str
    catalyst_event_identities: tuple[str, ...]
    catalyst_revision_identities: tuple[str, ...]
    catalyst_categories: tuple[str, ...]
    catalyst_quality_states: tuple[str, ...]
    catalyst_provider_tiers: tuple[str, ...]
    catalyst_freshness_states: tuple[str, ...]
    catalyst_pair_relevance: tuple[str, ...]
    catalyst_association_states: tuple[str, ...]
    catalyst_independent_provider_count: int
    catalyst_corroborated: bool
    catalyst_conflicting: bool
    catalyst_truncated: bool
    provider_availability_limited: bool
    regime_label: str
    regime_window: str
    btc_relative_strength: Decimal | None
    eth_relative_strength: Decimal | None
    horizon_seconds: int
    outcome_status: CryptoOutcomeStatus
    forward_return_percent: Decimal | None
    btc_relative_forward_return_percent: Decimal | None
    eth_relative_forward_return_percent: Decimal | None
    mae: Decimal | None
    mfe: Decimal | None
    mae_r: Decimal | None
    mfe_r: Decimal | None
    one_r_reached: bool | None
    two_r_reached: bool | None
    three_r_reached: bool | None
    stop_first: bool | None
    research_only: bool = True
    production_promoted: bool = False
    selection_authorized: bool = False
    execution_authorized: bool = False
    catalyst_statuses: tuple[str, ...] = ()
    catalyst_source_precisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.research_only or self.production_promoted or self.selection_authorized or self.execution_authorized:
            raise ValueError("G2 observations must remain research-only")
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ValueError("decision_cutoff must be timezone-aware")
        if self.horizon_seconds not in SUPPORTED_HORIZONS_SECONDS:
            raise ValueError("unsupported Phase D horizon")
        if len(self.catalyst_event_identities) > MAX_MEMBERSHIPS_PER_OBSERVATION:
            raise ValueError("catalyst membership bound exceeded")

    @property
    def qualified_catalyst_present(self) -> bool:
        return self.catalyst_state is CryptoCatalystCohortState.QUALIFIED_CATALYST

    @property
    def multi_membership(self) -> bool:
        return len(self.catalyst_categories) > 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "observation_identity": self.observation_identity,
            "asset_type": "CRYPTO",
            "canonical_pair": self.canonical_pair,
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "opportunity_identity": self.opportunity_identity,
            "detection_identity": self.detection_identity,
            "strategy_overlap_ids": self.strategy_overlap_ids,
            "catalyst_snapshot_identity": self.catalyst_snapshot_identity,
            "catalyst_snapshot_schema_version": self.catalyst_snapshot_schema_version,
            "catalyst_state": self.catalyst_state.value,
            "catalyst_overall_evidence_state": self.catalyst_overall_evidence_state,
            "catalyst_event_identities": self.catalyst_event_identities,
            "catalyst_revision_identities": self.catalyst_revision_identities,
            "catalyst_categories": self.catalyst_categories,
            "catalyst_quality_states": self.catalyst_quality_states,
            "catalyst_provider_tiers": self.catalyst_provider_tiers,
            "catalyst_freshness_states": self.catalyst_freshness_states,
            "catalyst_pair_relevance": self.catalyst_pair_relevance,
            "catalyst_association_states": self.catalyst_association_states,
            "catalyst_statuses": self.catalyst_statuses,
            "catalyst_source_precisions": self.catalyst_source_precisions,
            "catalyst_independent_provider_count": self.catalyst_independent_provider_count,
            "catalyst_corroborated": self.catalyst_corroborated,
            "catalyst_conflicting": self.catalyst_conflicting,
            "catalyst_truncated": self.catalyst_truncated,
            "provider_availability_limited": self.provider_availability_limited,
            "regime_label": self.regime_label,
            "regime_window": self.regime_window,
            "btc_relative_strength": _dec(self.btc_relative_strength),
            "eth_relative_strength": _dec(self.eth_relative_strength),
            "horizon_seconds": self.horizon_seconds,
            "outcome_status": self.outcome_status.value,
            "forward_return_percent": _dec(self.forward_return_percent),
            "btc_relative_forward_return_percent": _dec(self.btc_relative_forward_return_percent),
            "eth_relative_forward_return_percent": _dec(self.eth_relative_forward_return_percent),
            "mae": _dec(self.mae), "mfe": _dec(self.mfe), "mae_r": _dec(self.mae_r), "mfe_r": _dec(self.mfe_r),
            "one_r_reached": self.one_r_reached, "two_r_reached": self.two_r_reached,
            "three_r_reached": self.three_r_reached, "stop_first": self.stop_first,
            "research_only": True, "production_promoted": False,
            "selection_authorized": False, "execution_authorized": False,
        }


def build_crypto_catalyst_outcome_observation(
    decision: CryptoOutcomeDecision,
    snapshot: CryptoCatalystDecisionSnapshot,
    outcome: CryptoResearchOutcome,
) -> CryptoCatalystOutcomeObservation:
    """Link one Phase C/Phase D decision horizon to its frozen Phase G snapshot."""
    if outcome.decision is not decision and outcome.decision.identity.deterministic_id != decision.identity.deterministic_id:
        raise ValueError("outcome and decision identities do not match")
    if decision.decision_cutoff != snapshot.decision_cutoff:
        raise ValueError("snapshot cutoff does not match decision cutoff")
    if decision.canonical_symbol != snapshot.canonical_pair:
        raise ValueError("snapshot pair does not match decision pair")
    if outcome.horizon_seconds not in SUPPORTED_HORIZONS_SECONDS:
        raise ValueError("G2 only evaluates supported Phase D horizons")
    events = tuple(snapshot.events)
    categories = tuple(sorted({item.event_type for item in events}))
    qualities = tuple(sorted({item.quality_state.value for item in events}))
    tiers = tuple(sorted({item.provider_tier.value for item in events}))
    freshness = tuple(sorted({item.freshness.value for item in events}))
    relevance = tuple(sorted({item.relevance.value for item in events}))
    associations = tuple(sorted({item.association_state.value for item in events}))
    statuses = tuple(sorted({item.status for item in events}))
    precisions = tuple(sorted({item.source_precision for item in events}))
    providers = max((item.independent_provider_count for item in events), default=0)
    conflicting = snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.CONFLICTING_EVIDENCE or bool(snapshot.conflicts)
    overlap = tuple(sorted(set(getattr(decision, "strategy_memberships", ()))))
    opportunity = decision.identity.opportunity_id
    detection = decision.identity.deterministic_id
    semantic = (SCHEMA_VERSION, decision.identity.deterministic_id, snapshot.snapshot_identity, outcome.horizon_seconds)
    identity = semantic_digest("crypto-catalyst-outcome-observation", semantic)
    return CryptoCatalystOutcomeObservation(
        SCHEMA_VERSION, identity, decision.canonical_symbol, decision.decision_cutoff,
        decision.strategy_id, decision.strategy_version, opportunity, detection, overlap,
        snapshot.snapshot_identity, snapshot.snapshot_schema_version, _cohort_state(snapshot),
        snapshot.summary.overall_evidence_state.value,
        tuple(item.event_identity for item in events), tuple(item.revision_identity for item in events),
        categories, qualities, tiers, freshness, relevance, associations, providers,
        any(item.corroborated for item in events), conflicting, snapshot.truncated,
        _availability_limited(snapshot), decision.regime_at_decision.value,
        decision.regime_window_at_decision, decision.btc_relative_strength_at_decision,
        decision.eth_relative_strength_at_decision, outcome.horizon_seconds, outcome.status,
        outcome.forward_return_percent, outcome.btc_relative_forward_return_percent,
        outcome.eth_relative_forward_return_percent, outcome.mae, outcome.mfe, outcome.mae_r,
        outcome.mfe_r, outcome.one_r_reached, outcome.two_r_reached, outcome.three_r_reached,
        outcome.stop_reached if outcome.stop_reached is not None and outcome.first_event == "STOP" else (True if outcome.first_event == "STOP" else False if outcome.first_event else None),
        catalyst_statuses=statuses, catalyst_source_precisions=precisions,
    )


@dataclass(frozen=True, slots=True)
class CryptoCatalystOutcomeCohort:
    key: tuple[str, ...]
    sample_count: int
    sample_state: CryptoCatalystSampleState
    unique_observation_count: int
    multi_membership: bool
    mean_forward_return_percent: Decimal | None
    median_forward_return_percent: Decimal | None
    mean_btc_relative_return_percent: Decimal | None
    mean_eth_relative_return_percent: Decimal | None
    mean_mae: Decimal | None
    mean_mfe: Decimal | None
    mean_mae_r: Decimal | None
    mean_mfe_r: Decimal | None
    one_r_rate: Decimal | None
    two_r_rate: Decimal | None
    three_r_rate: Decimal | None
    stop_first_rate: Decimal | None

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "sample_count": self.sample_count, "sample_state": self.sample_state.value,
                "unique_observation_count": self.unique_observation_count, "multi_membership": self.multi_membership,
                "mean_forward_return_percent": _dec(self.mean_forward_return_percent),
                "median_forward_return_percent": _dec(self.median_forward_return_percent),
                "mean_btc_relative_return_percent": _dec(self.mean_btc_relative_return_percent),
                "mean_eth_relative_return_percent": _dec(self.mean_eth_relative_return_percent),
                "mean_mae": _dec(self.mean_mae), "mean_mfe": _dec(self.mean_mfe),
                "mean_mae_r": _dec(self.mean_mae_r), "mean_mfe_r": _dec(self.mean_mfe_r),
                "one_r_rate": _dec(self.one_r_rate), "two_r_rate": _dec(self.two_r_rate),
                "three_r_rate": _dec(self.three_r_rate), "stop_first_rate": _dec(self.stop_first_rate)}


@dataclass(frozen=True, slots=True)
class CryptoCatalystObservedDifference:
    """Descriptive qualified-vs-not-qualified comparison, never a causal claim."""

    qualified_sample_count: int
    nonqualified_sample_count: int
    qualified_mean_forward_return_percent: Decimal | None
    nonqualified_mean_forward_return_percent: Decimal | None
    observed_difference: Decimal | None
    sample_state: CryptoCatalystSampleState

    def to_dict(self) -> dict[str, object]:
        return {
            "qualified_sample_count": self.qualified_sample_count,
            "nonqualified_sample_count": self.nonqualified_sample_count,
            "qualified_mean_forward_return_percent": _dec(self.qualified_mean_forward_return_percent),
            "nonqualified_mean_forward_return_percent": _dec(self.nonqualified_mean_forward_return_percent),
            "observed_difference": _dec(self.observed_difference),
            "sample_state": self.sample_state.value,
            "causal_claim": False,
        }


@dataclass(frozen=True, slots=True)
class CryptoCatalystOutcomeEvaluation:
    schema_version: str
    report_identity: str
    observations: tuple[CryptoCatalystOutcomeObservation, ...]
    cohorts: tuple[CryptoCatalystOutcomeCohort, ...]
    observed_difference: CryptoCatalystObservedDifference | None
    candidate_observation_count: int
    retained_observation_count: int
    cohort_key_count: int
    truncated: bool
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("G2 evaluations must remain research-only")

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "report_identity": self.report_identity,
                "observations": tuple(item.to_dict() for item in self.observations),
                "cohorts": tuple(item.to_dict() for item in self.cohorts),
                "observed_difference": None if self.observed_difference is None else self.observed_difference.to_dict(),
                "candidate_observation_count": self.candidate_observation_count,
                "retained_observation_count": self.retained_observation_count,
                "cohort_key_count": self.cohort_key_count, "truncated": self.truncated,
                "research_only": True}


def _cohort_values(key: tuple[str, ...], observations: tuple[CryptoCatalystOutcomeObservation, ...]) -> tuple[CryptoCatalystOutcomeObservation, ...]:
    kind, value = key
    result = []
    for item in observations:
        values = {
            "strategy": (item.strategy_id,), "horizon": (str(item.horizon_seconds),),
            "strategy_horizon_regime": (f"{item.strategy_id}|{item.horizon_seconds}|{item.regime_label}",),
            "regime": (item.regime_label,), "catalyst_state": (item.catalyst_state.value,),
            "category": item.catalyst_categories, "provider_tier": item.catalyst_provider_tiers,
            "quality": item.catalyst_quality_states, "freshness": item.catalyst_freshness_states,
            "pair_relevance": item.catalyst_pair_relevance, "association": item.catalyst_association_states,
            "corroborated": (str(item.catalyst_corroborated).lower(),),
            "conflicting": (str(item.catalyst_conflicting).lower(),),
            "overlap_count": (str(len(item.strategy_overlap_ids)),),
            "event_status": item.catalyst_statuses,
            "source_precision": item.catalyst_source_precisions,
        }.get(kind, ())
        if value in values:
            result.append(item)
    return tuple(result)


def _make_cohort(key: tuple[str, ...], values: tuple[CryptoCatalystOutcomeObservation, ...]) -> CryptoCatalystOutcomeCohort:
    complete = tuple(item for item in values if item.outcome_status is CryptoOutcomeStatus.COMPLETE)
    return CryptoCatalystOutcomeCohort(
        key, len(complete), _sample_state(len(complete)), len({item.observation_identity for item in values}),
        any(item.multi_membership for item in values), _avg(item.forward_return_percent for item in complete),
        _med(item.forward_return_percent for item in complete), _avg(item.btc_relative_forward_return_percent for item in complete),
        _avg(item.eth_relative_forward_return_percent for item in complete), _avg(item.mae for item in complete),
        _avg(item.mfe for item in complete), _avg(item.mae_r for item in complete), _avg(item.mfe_r for item in complete),
        _rate(item.one_r_reached for item in complete), _rate(item.two_r_reached for item in complete),
        _rate(item.three_r_reached for item in complete), _rate(item.stop_first for item in complete),
    )


def evaluate_crypto_catalyst_outcomes(
    observations: Iterable[CryptoCatalystOutcomeObservation],
    *, maximum_observations: int = MAX_OBSERVATIONS,
    maximum_cohort_keys: int = MAX_COHORT_KEYS,
) -> CryptoCatalystOutcomeEvaluation:
    """Evaluate frozen observations descriptively and deterministically."""
    if not 1 <= maximum_observations <= MAX_OBSERVATIONS or not 1 <= maximum_cohort_keys <= MAX_COHORT_KEYS:
        raise ValueError("G2 bounds are invalid")
    all_items = tuple(observations)
    ordered = tuple(sorted(all_items, key=lambda item: (item.decision_cutoff, item.observation_identity)))
    unique: dict[str, CryptoCatalystOutcomeObservation] = {}
    for item in ordered:
        unique.setdefault(item.observation_identity, item)
    deduplicated = tuple(unique.values())
    retained = deduplicated[:maximum_observations]
    truncated = len(ordered) > maximum_observations
    keys: set[tuple[str, ...]] = set()
    for item in retained:
        keys.update({("strategy", item.strategy_id), ("horizon", str(item.horizon_seconds)), ("regime", item.regime_label),
                     ("strategy_horizon_regime", f"{item.strategy_id}|{item.horizon_seconds}|{item.regime_label}"),
                     ("catalyst_state", item.catalyst_state.value), ("corroborated", str(item.catalyst_corroborated).lower()),
                     ("conflicting", str(item.catalyst_conflicting).lower()), ("overlap_count", str(len(item.strategy_overlap_ids)))})
        keys.update(("category", value) for value in item.catalyst_categories)
        keys.update(("provider_tier", value) for value in item.catalyst_provider_tiers)
        keys.update(("quality", value) for value in item.catalyst_quality_states)
        keys.update(("freshness", value) for value in item.catalyst_freshness_states)
        keys.update(("pair_relevance", value) for value in item.catalyst_pair_relevance)
        keys.update(("association", value) for value in item.catalyst_association_states)
        keys.update(("event_status", value) for value in item.catalyst_statuses)
        keys.update(("source_precision", value) for value in item.catalyst_source_precisions)
    sorted_keys = tuple(sorted(keys))
    key_truncated = len(sorted_keys) > maximum_cohort_keys
    selected_keys = sorted_keys[:maximum_cohort_keys]
    cohorts = tuple(_make_cohort(key, _cohort_values(key, retained)) for key in selected_keys)
    qualified = tuple(item for item in retained if item.qualified_catalyst_present)
    unqualified = tuple(item for item in retained if not item.qualified_catalyst_present)
    difference = None
    if qualified and unqualified:
        qualified_mean = _avg(item.forward_return_percent for item in qualified if item.outcome_status is CryptoOutcomeStatus.COMPLETE)
        nonqualified_mean = _avg(item.forward_return_percent for item in unqualified if item.outcome_status is CryptoOutcomeStatus.COMPLETE)
        difference = CryptoCatalystObservedDifference(
            sum(item.outcome_status is CryptoOutcomeStatus.COMPLETE for item in qualified),
            sum(item.outcome_status is CryptoOutcomeStatus.COMPLETE for item in unqualified),
            qualified_mean, nonqualified_mean,
            None if qualified_mean is None or nonqualified_mean is None else qualified_mean - nonqualified_mean,
            _sample_state(min(len(qualified), len(unqualified))),
        )
    semantic = (SCHEMA_VERSION, tuple(item.to_dict() for item in retained), tuple(item.to_dict() for item in cohorts), None if difference is None else difference.to_dict(), truncated or key_truncated)
    return CryptoCatalystOutcomeEvaluation(SCHEMA_VERSION, semantic_digest("crypto-catalyst-outcome-evaluation", semantic), retained, cohorts, difference, len(all_items), len(retained), len(sorted_keys), truncated or key_truncated)


build_catalyst_outcome_observation = build_crypto_catalyst_outcome_observation
evaluate_catalyst_outcomes = evaluate_crypto_catalyst_outcomes


__all__ = [
    "SCHEMA_VERSION", "MAX_OBSERVATIONS", "MAX_COHORT_KEYS", "CryptoCatalystCohortState",
    "CryptoCatalystSampleState", "CryptoCatalystOutcomeObservation", "CryptoCatalystOutcomeCohort",
    "CryptoCatalystObservedDifference",
    "CryptoCatalystOutcomeEvaluation", "build_crypto_catalyst_outcome_observation",
    "build_catalyst_outcome_observation", "evaluate_crypto_catalyst_outcomes", "evaluate_catalyst_outcomes",
]
