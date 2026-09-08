from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.crypto_research import (
    CryptoAssociationConfidence,
    CryptoAssociationType,
    CryptoCatalystAggregator,
    CryptoCatalystDirection,
    CryptoCatalystEvidence,
    CryptoCatalystStatus,
    CryptoCatalystType,
    CryptoFreshness,
    CryptoProjectIdentity,
    CryptoProjectRegistry,
    CryptoPairAssociation,
    bootstrap_pair_identity,
    summarize_crypto_catalysts,
)


T0 = datetime(2026, 9, 8, 12, tzinfo=UTC)


def project(project_id="solana", symbol="SOL"):
    return CryptoProjectIdentity(project_id, project_id.title(), symbol, aliases=(symbol, project_id))


def evidence(*, provider="provider-a", key="upgrade-1", published=T0 - timedelta(hours=1), observed=T0 - timedelta(minutes=30), effective=None, revision=1, status=CryptoCatalystStatus.ANNOUNCED, confidence=CryptoAssociationConfidence.EXACT, project_value=None, event_type=CryptoCatalystType.PROTOCOL_UPGRADE):
    item = project_value or project()
    pair = bootstrap_pair_identity(f"{item.canonical_token_symbol}/USD", f"{item.canonical_token_symbol}USD", "instrument-1")
    association = CryptoPairAssociation(pair, CryptoAssociationType.PROJECT, confidence, "fixture registry mapping")
    return CryptoCatalystEvidence(
        event_type, status, provider, provider.upper(), f"{provider}:{key}", published,
        observed, effective, item, item.canonical_token_symbol, (association,), confidence,
        CryptoCatalystDirection.UNKNOWN, "Protocol upgrade announcement", "Fixture evidence",
        provider_event_id=key, underlying_event_key=key, revision=revision,
    )


def test_project_token_pair_are_distinct_and_pair_bootstrap_does_not_claim_project():
    pair = bootstrap_pair_identity("SOL/USD", "SOLUSD", "webull-sol")
    assert pair.canonical_pair == "SOL/USD"
    assert pair.instrument_id == "webull-sol"
    assert project().project_id != pair.canonical_pair


def test_registry_verified_alias_and_collision_are_explicit():
    first = project("alpha", "DUP")
    second = project("beta", "DUP")
    registry = CryptoProjectRegistry((first, second))
    assert registry.lookup("alpha").confidence is CryptoAssociationConfidence.EXACT
    assert registry.lookup("DUP").confidence is CryptoAssociationConfidence.AMBIGUOUS
    assert registry.lookup("missing").confidence is CryptoAssociationConfidence.UNRESOLVED
    assert registry.lookup("alpha").matches == (first,)


def test_event_taxonomy_timestamps_and_future_effective_time():
    item = evidence(effective=T0 + timedelta(days=1), event_type=CryptoCatalystType.TOKEN_UNLOCK)
    assert item.event_type is CryptoCatalystType.TOKEN_UNLOCK
    assert item.published_at < item.observed_at < item.effective_at
    assert item.eligible_at(T0)
    assert item.freshness_at(T0) is CryptoFreshness.FRESH


def test_future_published_evidence_is_rejected_without_lookahead():
    future = evidence(published=T0 + timedelta(minutes=1), observed=T0 + timedelta(minutes=2))
    result = CryptoCatalystAggregator().aggregate((future,), decision_cutoff=T0)
    assert result.events == ()
    assert result.rejected_future == 1


def test_same_event_is_deduplicated_and_independent_sources_corroborate():
    one = evidence(provider="provider-a")
    two = evidence(provider="provider-b")
    syndicated = evidence(provider="provider-a", key="upgrade-1")
    result = CryptoCatalystAggregator().aggregate((one, two, syndicated), decision_cutoff=T0)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.independent_provider_count == 2
    assert event.corroborated
    assert result.metrics.crypto_catalyst_duplicates_suppressed == 1


def test_same_provider_syndication_is_suppressed_even_when_reference_differs():
    one = evidence(provider="provider-a")
    two = CryptoCatalystEvidence(
        one.event_type, one.status, one.provider_id, one.source_name, "provider-a:syndicated-copy",
        one.published_at, one.observed_at, one.effective_at, one.project, one.token_symbol,
        one.associated_pairs, one.association_confidence, one.expected_direction, one.title,
        one.summary, "https://mirror.example/event", one.provider_event_id, one.underlying_event_key,
        one.revision, one.supersedes, one.provenance, one.decision_cutoff, one.freshness_policy_seconds,
    )
    result = CryptoCatalystAggregator().aggregate((one, two), decision_cutoff=T0)
    assert len(result.events) == 1
    assert result.metrics.crypto_catalyst_duplicates_suppressed == 1


def test_revision_chain_is_retained_and_prior_cutoff_replays_old_knowledge():
    original = evidence(provider="provider-a", revision=1, status=CryptoCatalystStatus.SCHEDULED)
    revised = evidence(provider="provider-a", revision=2, status=CryptoCatalystStatus.DELAYED, observed=T0 + timedelta(hours=1))
    aggregator = CryptoCatalystAggregator()
    before = aggregator.aggregate((original, revised), decision_cutoff=T0)
    assert len(before.events) == 1
    assert len(before.events[0].revisions) == 1
    assert before.events[0].latest.status is CryptoCatalystStatus.SCHEDULED
    after = aggregator.aggregate((original, revised), decision_cutoff=T0 + timedelta(hours=2))
    assert len(after.events[0].revisions) == 2
    assert after.events[0].latest.status is CryptoCatalystStatus.DELAYED


def test_status_direction_and_association_are_not_bullish_assumptions():
    item = evidence(status=CryptoCatalystStatus.CANCELLED, confidence=CryptoAssociationConfidence.AMBIGUOUS)
    assert item.expected_direction is CryptoCatalystDirection.UNKNOWN
    assert item.association_confidence is CryptoAssociationConfidence.AMBIGUOUS


def test_bounded_retention_is_deterministic_and_metrics_are_scalar():
    aggregator = CryptoCatalystAggregator(maximum_events_per_project=2, maximum_canonical_events=3, maximum_revision_identities=4)
    items = tuple(evidence(key=f"event-{index}", project_value=project("solana", "SOL")) for index in range(6))
    result = aggregator.aggregate(items, decision_cutoff=T0)
    assert len(result.events) <= 3
    assert result.metrics.crypto_catalyst_events_retained <= 3
    assert result.metrics.crypto_catalyst_events_evicted > 0
    assert isinstance(result.metrics.crypto_catalyst_ambiguous, int)


def test_provider_failure_is_contained():
    class Broken:
        provider_id = "broken"
        def collect(self, as_of):
            raise RuntimeError("fixture failure")

    result = CryptoCatalystAggregator().aggregate_provider(Broken(), decision_cutoff=T0)
    assert result.events == ()
    assert result.metrics.crypto_catalyst_provider_failures == 1


def test_summary_is_explicit_and_read_only():
    result = CryptoCatalystAggregator().aggregate((evidence(),), decision_cutoff=T0)
    summary = summarize_crypto_catalysts(result, decision_cutoff=T0)
    assert summary.events_by_type == ((CryptoCatalystType.PROTOCOL_UPGRADE.value, 1),)
    assert summary.events_by_project == (("solana", 1),)
    assert summary.fresh_count == 1
    assert summary.stale_count == 0


def test_evidence_rejects_naive_timestamps_and_non_research_authority():
    with pytest.raises(ValueError, match="timezone-aware"):
        evidence(published=datetime(2026, 9, 8, 12))
    with pytest.raises(ValueError, match="research-only"):
        CryptoCatalystEvidence(CryptoCatalystType.MACRO_EVENT, CryptoCatalystStatus.ANNOUNCED, "p", "P", "r", T0, T0, research_only=False)
