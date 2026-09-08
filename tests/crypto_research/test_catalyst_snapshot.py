from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.crypto_research import (
    CryptoAssociationConfidence,
    CryptoCatalystDirection,
    CryptoCatalystEvidence,
    CryptoCatalystOverallState,
    CryptoCatalystPairRelevance,
    CryptoCatalystProviderAvailability,
    CryptoCatalystProviderAvailabilityRecord,
    CryptoCatalystProviderTier,
    CryptoCatalystQualityState,
    CryptoCatalystStatus,
    CryptoCatalystType,
    CryptoPairIdentity,
    build_crypto_catalyst_snapshot,
)
from app.research_core import EvidenceProvenance


UTC0 = UTC
CUTOFF = datetime(2026, 9, 8, 15, tzinfo=UTC0)
PAIR = CryptoPairIdentity("SOL/USD", "SOLUSD", base_asset_id="sol-native", quote_asset_id="usd")


def evidence(
    *,
    provider="STATUSPAGE",
    event_type=CryptoCatalystType.NETWORK_OUTAGE,
    status=CryptoCatalystStatus.ACTIVE,
    published=datetime(2026, 9, 8, 14, tzinfo=UTC0),
    observed=datetime(2026, 9, 8, 14, 30, tzinfo=UTC0),
    confidence=CryptoAssociationConfidence.PROVIDER_MAPPED,
    freshness_policy_seconds=None,
    key="incident-1",
    revision=1,
):
    return CryptoCatalystEvidence(
        event_type=event_type,
        status=status,
        provider_id=provider,
        source_name=provider,
        source_reference=f"{provider}:{key}:{revision}",
        provider_event_id=key,
        underlying_event_key=key,
        published_at=published,
        observed_at=observed,
        decision_cutoff=None,
        association_confidence=confidence,
        expected_direction=CryptoCatalystDirection.UNKNOWN,
        associated_pairs=(),
        freshness_policy_seconds=freshness_policy_seconds,
        title="structured evidence",
        provenance=EvidenceProvenance(observed_at=observed, decision_cutoff=observed, evidence_timestamps=(("published_at", published),)),
        revision=revision,
    )


def build(items, *, decision_cutoff=CUTOFF, **kwargs):
    return build_crypto_catalyst_snapshot(pair=PAIR, decision_cutoff=decision_cutoff, evidence=items, **kwargs)


def test_snapshot_freezes_cutoff_and_is_deterministic():
    item = evidence()
    tiers = {"STATUSPAGE": CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY}
    first = build([item], provider_tiers=tiers)
    second = build([item], provider_tiers=tiers)
    assert first.snapshot_identity == second.snapshot_identity
    assert first.events[0].relevance is CryptoCatalystPairRelevance.NETWORK
    assert first.events[0].quality_state is CryptoCatalystQualityState.QUALIFIED
    assert first.summary.overall_evidence_state is CryptoCatalystOverallState.QUALIFIED_EVIDENCE


def test_future_published_or_observed_evidence_is_excluded():
    future = evidence(published=CUTOFF + timedelta(minutes=1), observed=CUTOFF + timedelta(minutes=2))
    snapshot = build([future])
    assert snapshot.events == ()
    assert snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.NO_EVIDENCE


def test_future_effective_event_is_allowed_without_lookahead():
    item = CryptoCatalystEvidence(
        event_type=CryptoCatalystType.REGULATORY_ACTION,
        status=CryptoCatalystStatus.SCHEDULED,
        provider_id="FEDERAL_REGISTER",
        source_name="Federal Register",
        source_reference="2026-17183",
        published_at=datetime(2026, 9, 1, tzinfo=UTC0),
        effective_at=datetime(2026, 10, 1, tzinfo=UTC0),
        observed_at=datetime(2026, 9, 2, tzinfo=UTC0),
        association_confidence=CryptoAssociationConfidence.MULTI_ASSET,
        provenance=EvidenceProvenance(observed_at=datetime(2026, 9, 2, tzinfo=UTC0), decision_cutoff=CUTOFF),
    )
    snapshot = build([item], provider_tiers={"FEDERAL_REGISTER": CryptoCatalystProviderTier.OFFICIAL_REGULATORY}, source_precisions={"FEDERAL_REGISTER": "DATE"})
    assert snapshot.events[0].effective_at == datetime(2026, 10, 1, tzinfo=UTC0)
    assert snapshot.events[0].source_precision == "DATE"


def test_ambiguous_and_unresolved_are_not_qualified():
    snapshot = build([evidence(confidence=CryptoAssociationConfidence.AMBIGUOUS, key="a"), evidence(confidence=CryptoAssociationConfidence.UNRESOLVED, key="u")])
    assert {item.quality_state for item in snapshot.events} == {CryptoCatalystQualityState.AMBIGUOUS, CryptoCatalystQualityState.UNRESOLVED}
    assert snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.AMBIGUOUS_EVIDENCE


def test_stale_is_explicit_not_zero():
    old = evidence(published=CUTOFF - timedelta(days=3), observed=CUTOFF - timedelta(days=2), freshness_policy_seconds=60)
    snapshot = build([old], provider_tiers={"STATUSPAGE": CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY})
    assert snapshot.events[0].freshness.value == "STALE"
    assert snapshot.events[0].quality_state is CryptoCatalystQualityState.STALE
    assert snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.STALE_ONLY


def test_provider_blocked_is_distinct_from_no_event():
    availability = CryptoCatalystProviderAvailabilityRecord(
        "BYBIT", CryptoCatalystProviderAvailability.BLOCKED, CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY,
        failure_category="GEOGRAPHIC_PROVIDER_RESTRICTION", evidence_cutoff=CUTOFF,
    )
    snapshot = build([], provider_availability=[availability])
    assert snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.UNAVAILABLE
    assert snapshot.provider_availability[0].availability_state is CryptoCatalystProviderAvailability.BLOCKED


def test_truncation_is_visible_and_bounded():
    items = [evidence(key=f"incident-{index}") for index in range(5)]
    snapshot = build(items, maximum_retained_events=2)
    assert snapshot.truncated is True
    assert snapshot.candidate_event_count == 5
    assert snapshot.retained_event_count == 2


def test_conflicts_are_explicit():
    snapshot = build([evidence()], conflicts=["effective_date_conflict"])
    assert snapshot.summary.overall_evidence_state is CryptoCatalystOverallState.CONFLICTING_EVIDENCE
    assert snapshot.conflicts == ("effective_date_conflict",)


def test_revisions_and_later_resolution_do_not_rewrite_earlier_snapshot():
    active = evidence(key="incident", status=CryptoCatalystStatus.ACTIVE, published=datetime(2026, 9, 8, 10, tzinfo=UTC0), observed=datetime(2026, 9, 8, 10, 1, tzinfo=UTC0))
    resolved = evidence(key="incident", status=CryptoCatalystStatus.RESOLVED, event_type=CryptoCatalystType.NETWORK_RECOVERY, published=datetime(2026, 9, 8, 11, tzinfo=UTC0), observed=datetime(2026, 9, 8, 11, 1, tzinfo=UTC0), revision=2)
    before = build([active, resolved], decision_cutoff=datetime(2026, 9, 8, 10, 30, tzinfo=UTC0))
    after = build([active, resolved])
    assert before.events[0].status == CryptoCatalystStatus.ACTIVE.value
    assert any(item.status == CryptoCatalystStatus.RESOLVED.value for item in after.events)
    assert before.snapshot_identity != after.snapshot_identity


def test_research_authority_flags_are_frozen():
    item = build([evidence()]).events[0]
    assert item.research_only is True
    assert item.production_promoted is False
    assert item.selection_authorized is False
    assert item.execution_authorized is False


def test_independent_provider_corroboration_is_cutoff_aware():
    first = evidence(provider="SEC_EDGAR", key="shared", published=datetime(2026, 9, 8, 9, tzinfo=UTC0), observed=datetime(2026, 9, 8, 10, 1, tzinfo=UTC0))
    second = evidence(provider="FEDERAL_REGISTER", key="shared", published=datetime(2026, 9, 8, 9, tzinfo=UTC0), observed=datetime(2026, 9, 8, 11, 1, tzinfo=UTC0))
    before = build([first, second], decision_cutoff=datetime(2026, 9, 8, 10, 30, tzinfo=UTC0))
    after = build([first, second], provider_tiers={"SEC_EDGAR": CryptoCatalystProviderTier.OFFICIAL_REGULATORY, "FEDERAL_REGISTER": CryptoCatalystProviderTier.OFFICIAL_REGULATORY})
    assert {item.independent_provider_count for item in before.events} == {1}
    assert all(item.corroborated is False for item in before.events)
    assert {item.independent_provider_count for item in after.events} == {2}
    assert all(item.corroborated is True for item in after.events)
    assert before.snapshot_identity != after.snapshot_identity


def test_same_provider_revisions_count_once():
    first = evidence(provider="STATUSPAGE", key="incident", revision=1)
    second = evidence(provider="STATUSPAGE", key="incident", revision=2, status=CryptoCatalystStatus.RESOLVED, event_type=CryptoCatalystType.NETWORK_RECOVERY)
    snapshot = build([first, second])
    assert {item.independent_provider_count for item in snapshot.events} == {1}
    assert all(item.corroborated is False for item in snapshot.events)


def test_cross_provider_status_conflict_is_derived_only_when_eligible():
    first = evidence(provider="SEC_EDGAR", key="shared", status=CryptoCatalystStatus.SCHEDULED, published=datetime(2026, 9, 8, 9, tzinfo=UTC0), observed=datetime(2026, 9, 8, 10, 1, tzinfo=UTC0))
    second = evidence(provider="FEDERAL_REGISTER", key="shared", status=CryptoCatalystStatus.CANCELLED, published=datetime(2026, 9, 8, 9, tzinfo=UTC0), observed=datetime(2026, 9, 8, 11, 1, tzinfo=UTC0))
    before = build([first, second], decision_cutoff=datetime(2026, 9, 8, 10, 30, tzinfo=UTC0))
    after = build([first, second])
    assert before.summary.overall_evidence_state is not CryptoCatalystOverallState.CONFLICTING_EVIDENCE
    assert after.summary.overall_evidence_state is CryptoCatalystOverallState.CONFLICTING_EVIDENCE
    assert all("STATUS_CONFLICT" in item.quality_reasons for item in after.events)


def test_compatible_independent_providers_corroborate_without_conflict():
    first = evidence(provider="SEC_EDGAR", key="shared")
    second = evidence(provider="FEDERAL_REGISTER", key="shared")
    snapshot = build([second, first])
    assert snapshot.summary.overall_evidence_state is not CryptoCatalystOverallState.CONFLICTING_EVIDENCE
    assert all(item.corroborated is True for item in snapshot.events)


def test_unrelated_provider_events_do_not_corroborate_or_conflict():
    first = evidence(provider="SEC_EDGAR", key="event-a")
    second = evidence(provider="FEDERAL_REGISTER", key="event-b", status=CryptoCatalystStatus.CANCELLED)
    snapshot = build([first, second])
    assert all(item.independent_provider_count == 1 for item in snapshot.events)
    assert snapshot.summary.overall_evidence_state is not CryptoCatalystOverallState.CONFLICTING_EVIDENCE


def test_single_authoritative_provider_can_qualify_without_corroboration():
    snapshot = build([evidence(provider="SEC_EDGAR")], provider_tiers={"SEC_EDGAR": CryptoCatalystProviderTier.OFFICIAL_REGULATORY})
    assert snapshot.events[0].quality_state is CryptoCatalystQualityState.QUALIFIED
    assert snapshot.events[0].corroborated is False


def test_input_order_does_not_change_snapshot_identity():
    first = evidence(provider="SEC_EDGAR", key="a")
    second = evidence(provider="FEDERAL_REGISTER", key="b")
    left = build([first, second])
    right = build([second, first])
    assert left.snapshot_identity == right.snapshot_identity


def test_corroboration_and_conflict_are_distinct_but_can_coexist():
    first = evidence(provider="SEC_EDGAR", key="shared", status=CryptoCatalystStatus.SCHEDULED)
    second = evidence(provider="FEDERAL_REGISTER", key="shared", status=CryptoCatalystStatus.CANCELLED)
    snapshot = build([first, second])
    assert all(item.independent_provider_count == 2 for item in snapshot.events)
    assert all(item.corroborated is True for item in snapshot.events)
    assert all(item.quality_state is CryptoCatalystQualityState.CONFLICTING for item in snapshot.events)
