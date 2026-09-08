from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from dataclasses import replace

import pytest

from app.assets import AssetType
from app.crypto_research import (
    CryptoAssociationConfidence, CryptoCatalystDirection, CryptoCatalystEvidence,
    CryptoCatalystProviderAvailability, CryptoCatalystProviderAvailabilityRecord,
    CryptoCatalystProviderTier, CryptoCatalystStatus, CryptoCatalystType,
    CryptoOutcomeDecision, CryptoOutcomeStatus, CryptoPairIdentity,
    build_crypto_catalyst_outcome_observation, build_crypto_catalyst_snapshot,
    evaluate_crypto_catalyst_outcomes,
)
from app.crypto_research.catalyst_outcomes import (
    CryptoCatalystCohortState, CryptoCatalystSampleState,
)
from app.research_core import AssetResearchIdentity, EvidenceProvenance


CUTOFF = datetime(2026, 9, 8, 15, tzinfo=UTC)
PAIR = CryptoPairIdentity("SOL/USD", "SOLUSD", base_asset_id="sol", quote_asset_id="usd")


def decision(*, opportunity="opp-1", memberships=("MICRO_PULLBACK",)):
    identity = AssetResearchIdentity(AssetType.CRYPTO, "SOL/USD", memberships[0], "crypto-phase-c-v1", CUTOFF, opportunity, "episode-1")
    return CryptoOutcomeDecision(identity, "SOL/USD", memberships[0], "crypto-phase-c-v1", CUTOFF, CUTOFF,
        Decimal("100"), Decimal("95"), Decimal("5"), __import__("app.crypto_research", fromlist=["CryptoRegimeLabel"]).CryptoRegimeLabel.BROAD_RISK_ON,
        "US", Decimal("2"), Decimal("3"), Decimal("0.4"), Decimal("0.5"), Decimal("66"), Decimal("1.2"), tuple(memberships), "cohort", EvidenceProvenance(CUTOFF, CUTOFF))


def evidence(provider="SEC", *, key="event", status=CryptoCatalystStatus.ACTIVE, event_type=CryptoCatalystType.REGULATORY_ACTION, published=CUTOFF.replace(hour=14), observed=CUTOFF.replace(hour=14, minute=30)):
    return CryptoCatalystEvidence(event_type=event_type, status=status, provider_id=provider,
        source_name=provider, source_reference=f"{provider}:{key}", provider_event_id=key, underlying_event_key=key,
        published_at=published, observed_at=observed, association_confidence=CryptoAssociationConfidence.MULTI_ASSET,
        expected_direction=CryptoCatalystDirection.UNKNOWN, provenance=EvidenceProvenance(observed, observed))


def snapshot(items=(), *, availability=()):
    return build_crypto_catalyst_snapshot(pair=PAIR, decision_cutoff=CUTOFF, evidence=items,
        provider_tiers={"SEC": CryptoCatalystProviderTier.OFFICIAL_REGULATORY, "FED": CryptoCatalystProviderTier.OFFICIAL_REGULATORY},
        provider_availability=availability)


def outcome(item_decision, horizon=60, *, status=CryptoOutcomeStatus.COMPLETE, value="2"):
    return __import__("app.crypto_research", fromlist=["CryptoResearchOutcome"]).CryptoResearchOutcome(
        item_decision, horizon, CUTOFF, status, Decimal(value), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
        Decimal("-1"), Decimal("3"), Decimal("-0.2"), Decimal("0.6"), False, True, False, False)


def test_one_observation_per_decision_horizon_and_frozen_identity():
    d = decision()
    snap = snapshot([evidence()])
    first = build_crypto_catalyst_outcome_observation(d, snap, outcome(d))
    second = build_crypto_catalyst_outcome_observation(d, snap, outcome(d))
    assert first.observation_identity == second.observation_identity
    report = evaluate_crypto_catalyst_outcomes([first, second])
    assert report.retained_observation_count == 1
    assert len({item.observation_identity for item in report.observations}) == 1


def test_snapshot_is_consumed_not_recomputed_and_provider_unavailable_is_distinct():
    d = decision()
    blocked = snapshot(availability=(CryptoCatalystProviderAvailabilityRecord("BYBIT", CryptoCatalystProviderAvailability.BLOCKED, CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY),))
    item = build_crypto_catalyst_outcome_observation(d, blocked, outcome(d))
    assert item.catalyst_state is CryptoCatalystCohortState.PROVIDER_AVAILABILITY_LIMITED
    assert item.provider_availability_limited and item.catalyst_event_identities == ()


def test_multiple_categories_are_non_additive_memberships():
    d = decision(memberships=("MICRO_PULLBACK", "MOMENTUM_REACCELERATION"))
    item = build_crypto_catalyst_outcome_observation(d, snapshot([evidence(), evidence(provider="FED", key="second", event_type=CryptoCatalystType.NETWORK_OUTAGE)]), outcome(d))
    report = evaluate_crypto_catalyst_outcomes([item])
    assert item.multi_membership is True
    assert report.candidate_observation_count == 1
    assert report.observed_difference is None


def test_all_supported_horizons_and_missing_r_are_preserved():
    d = decision()
    snap = snapshot([evidence()])
    observations = [build_crypto_catalyst_outcome_observation(d, snap, outcome(d, horizon)) for horizon in (60, 300, 900, 1800)]
    report = evaluate_crypto_catalyst_outcomes(observations)
    assert {item.horizon_seconds for item in report.observations} == {60, 300, 900, 1800}
    assert all(item.mae_r is not None for item in report.observations)


def test_quality_states_and_single_authoritative_source_are_descriptive():
    d = decision()
    item = build_crypto_catalyst_outcome_observation(d, snapshot([evidence()]), outcome(d))
    assert item.catalyst_state is CryptoCatalystCohortState.QUALIFIED_CATALYST
    report = evaluate_crypto_catalyst_outcomes([item])
    assert report.cohorts and report.cohorts[0].sample_state is CryptoCatalystSampleState.INSUFFICIENT


def test_deterministic_evaluation_and_bounds_are_explicit():
    items = []
    base = decision()
    snap = snapshot([evidence()])
    for index in range(8):
        d = decision(opportunity=f"opp-{index}")
        items.append(build_crypto_catalyst_outcome_observation(d, snap, outcome(d, 60, value=str(index))))
    one = evaluate_crypto_catalyst_outcomes(reversed(items))
    two = evaluate_crypto_catalyst_outcomes(items)
    assert one.report_identity == two.report_identity
    assert one.to_dict() == two.to_dict()
    limited = evaluate_crypto_catalyst_outcomes(items, maximum_observations=2)
    assert limited.truncated and limited.retained_observation_count == 2


def test_future_cutoff_mismatch_is_rejected():
    d = decision()
    later = build_crypto_catalyst_snapshot(pair=PAIR, decision_cutoff=CUTOFF.replace(hour=16), evidence=())
    with pytest.raises(ValueError, match="cutoff"):
        build_crypto_catalyst_outcome_observation(d, later, outcome(d))


def test_research_authority_is_frozen():
    d = decision()
    item = build_crypto_catalyst_outcome_observation(d, snapshot(), outcome(d))
    assert item.research_only is True
    assert not item.production_promoted and not item.selection_authorized and not item.execution_authorized


def test_sample_gates_are_the_existing_phase_d_boundaries():
    d = decision()
    item = build_crypto_catalyst_outcome_observation(d, snapshot(), outcome(d))
    assert evaluate_crypto_catalyst_outcomes([replace(item, observation_identity=f"x-{i}") for i in range(10)]).cohorts[0].sample_state is CryptoCatalystSampleState.INSUFFICIENT
    assert evaluate_crypto_catalyst_outcomes([replace(item, observation_identity=f"x-{i}") for i in range(11)]).cohorts[0].sample_state is CryptoCatalystSampleState.EARLY
    assert evaluate_crypto_catalyst_outcomes([replace(item, observation_identity=f"x-{i}") for i in range(29)]).cohorts[0].sample_state is CryptoCatalystSampleState.EARLY
    assert evaluate_crypto_catalyst_outcomes([replace(item, observation_identity=f"x-{i}") for i in range(30)]).cohorts[0].sample_state is CryptoCatalystSampleState.RESEARCH


def test_later_snapshot_is_a_new_context_not_a_mutation():
    d = decision()
    early = snapshot([evidence()])
    later = snapshot([evidence(), evidence(provider="FED", key="corroboration")])
    first = build_crypto_catalyst_outcome_observation(d, early, outcome(d))
    second = build_crypto_catalyst_outcome_observation(d, later, outcome(d))
    assert first.catalyst_snapshot_identity != second.catalyst_snapshot_identity
    assert first.observation_identity != second.observation_identity
    assert first.catalyst_independent_provider_count == 1
    assert second.catalyst_independent_provider_count == 1  # distinct event identities remain distinct
