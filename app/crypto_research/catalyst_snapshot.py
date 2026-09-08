"""Immutable, point-in-time crypto catalyst decision snapshots.

This module consumes normalized :mod:`catalysts` evidence only.  It performs no
network access, strategy selection, scoring, sizing, or execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable, Mapping

from app.assets import AssetType
from app.research_core import semantic_digest

from .catalysts import (
    CryptoAssociationConfidence,
    CryptoCatalystEvidence,
    CryptoCatalystStatus,
    CryptoFreshness,
    CryptoPairIdentity,
)


SNAPSHOT_SCHEMA_VERSION = "crypto-catalyst-decision-snapshot-v1"
MAX_CANDIDATE_EVENTS = 4096
MAX_RETAINED_EVENTS = 128
MAX_QUALITY_REASONS = 16
MAX_CONFLICTS = 32


class CryptoCatalystPairRelevance(StrEnum):
    DIRECT_PAIR = "DIRECT_PAIR"
    BASE_ASSET = "BASE_ASSET"
    QUOTE_ASSET = "QUOTE_ASSET"
    PROJECT = "PROJECT"
    NETWORK = "NETWORK"
    MULTI_ASSET = "MULTI_ASSET"
    MARKET_WIDE = "MARKET_WIDE"
    UNRELATED = "UNRELATED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class CryptoCatalystProviderTier(StrEnum):
    AUTHORITATIVE_PRIMARY = "AUTHORITATIVE_PRIMARY"
    OFFICIAL_FIRST_PARTY = "OFFICIAL_FIRST_PARTY"
    OFFICIAL_REGULATORY = "OFFICIAL_REGULATORY"
    SECONDARY_VERIFIED = "SECONDARY_VERIFIED"
    CORROBORATION_ONLY = "CORROBORATION_ONLY"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class CryptoCatalystQualityState(StrEnum):
    QUALIFIED = "QUALIFIED"
    QUALIFIED_WITH_LIMITATIONS = "QUALIFIED_WITH_LIMITATIONS"
    INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"
    INSUFFICIENT_ASSOCIATION = "INSUFFICIENT_ASSOCIATION"
    INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"
    STALE = "STALE"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


class CryptoCatalystOverallState(StrEnum):
    NO_EVIDENCE = "NO_EVIDENCE"
    QUALIFIED_EVIDENCE = "QUALIFIED_EVIDENCE"
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"
    AMBIGUOUS_EVIDENCE = "AMBIGUOUS_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_ONLY = "STALE_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


class CryptoCatalystProviderAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED_BY_PROVIDER_GEOGRAPHIC_POLICY"
    FAILED = "FAILED"
    NOT_QUERIED = "NOT_QUERIED"


@dataclass(frozen=True, slots=True)
class CryptoCatalystProviderAvailabilityRecord:
    provider_id: str
    availability_state: CryptoCatalystProviderAvailability
    source_tier: CryptoCatalystProviderTier
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    failure_category: str | None = None
    evidence_cutoff: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", self.provider_id.strip())
        if not self.provider_id:
            raise ValueError("provider_id is required")
        for name in ("last_success_at", "last_attempt_at", "evidence_cutoff"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{name} must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        if self.failure_category:
            object.__setattr__(self, "failure_category", self.failure_category.strip())


@dataclass(frozen=True, slots=True)
class CryptoCatalystDecisionEvent:
    event_identity: str
    revision_identity: str
    event_type: str
    direction: str
    status: str
    project_id: str | None
    token_symbol: str | None
    network_id: str | None
    canonical_pairs: tuple[str, ...]
    relevance: CryptoCatalystPairRelevance
    association_state: CryptoAssociationConfidence
    published_at: datetime
    effective_at: datetime | None
    observed_at: datetime
    age_seconds: int
    freshness: CryptoFreshness
    freshness_policy_version: str
    provider_id: str
    provider_tier: CryptoCatalystProviderTier
    source_precision: str
    source_reference: str
    corroborated: bool
    independent_provider_count: int
    revision_number: int
    supersedes: str | None
    evidence_complete: bool
    quality_state: CryptoCatalystQualityState
    quality_reasons: tuple[str, ...]
    research_only: bool = True
    production_promoted: bool = False
    selection_authorized: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.research_only or self.production_promoted or self.selection_authorized or self.execution_authorized:
            raise ValueError("decision catalyst evidence must remain research-only")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.effective_at is not None and (self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None):
            raise ValueError("effective_at must be timezone-aware")
        if len(self.quality_reasons) > MAX_QUALITY_REASONS:
            raise ValueError("quality reason bound exceeded")

    @property
    def identity(self) -> str:
        return self.event_identity

    def to_dict(self) -> dict[str, object]:
        return {
            "event_identity": self.event_identity,
            "revision_identity": self.revision_identity,
            "event_type": self.event_type,
            "direction": self.direction,
            "status": self.status,
            "project_id": self.project_id,
            "token_symbol": self.token_symbol,
            "network_id": self.network_id,
            "canonical_pairs": self.canonical_pairs,
            "relevance": self.relevance.value,
            "association_state": self.association_state.value,
            "published_at": self.published_at.isoformat(),
            "effective_at": self.effective_at.isoformat() if self.effective_at else None,
            "observed_at": self.observed_at.isoformat(),
            "age_seconds": self.age_seconds,
            "freshness": self.freshness.value,
            "freshness_policy_version": self.freshness_policy_version,
            "provider_id": self.provider_id,
            "provider_tier": self.provider_tier.value,
            "source_precision": self.source_precision,
            "source_reference": self.source_reference,
            "corroborated": self.corroborated,
            "independent_provider_count": self.independent_provider_count,
            "revision_number": self.revision_number,
            "supersedes": self.supersedes,
            "evidence_complete": self.evidence_complete,
            "quality_state": self.quality_state.value,
            "quality_reasons": self.quality_reasons,
            "research_only": True,
            "production_promoted": False,
            "selection_authorized": False,
            "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class CryptoCatalystSnapshotSummary:
    eligible_event_count: int
    qualified_event_count: int
    limited_event_count: int
    stale_event_count: int
    ambiguous_event_count: int
    unresolved_event_count: int
    conflicting_event_count: int
    fresh_event_count: int
    independent_provider_count: int
    event_categories_present: tuple[str, ...]
    latest_qualified_event_age: int | None
    highest_provider_tier: CryptoCatalystProviderTier | None
    has_network_incident: bool
    has_regulatory_event: bool
    has_exchange_event: bool
    has_security_event: bool
    overall_evidence_state: CryptoCatalystOverallState


@dataclass(frozen=True, slots=True)
class CryptoCatalystDecisionSnapshot:
    asset_type: AssetType
    canonical_pair: str
    decision_cutoff: datetime
    snapshot_schema_version: str
    snapshot_identity: str
    base_asset_id: str | None
    quote_asset_id: str | None
    project_id: str | None
    network_id: str | None
    events: tuple[CryptoCatalystDecisionEvent, ...]
    summary: CryptoCatalystSnapshotSummary
    provider_availability: tuple[CryptoCatalystProviderAvailabilityRecord, ...]
    candidate_event_count: int
    retained_event_count: int
    truncated: bool
    conflicts: tuple[str, ...]
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.asset_type is not AssetType.CRYPTO or not self.research_only:
            raise ValueError("crypto catalyst snapshots are research-only")
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ValueError("decision_cutoff must be timezone-aware")
        if self.retained_event_count != len(self.events) or self.retained_event_count > MAX_RETAINED_EVENTS:
            raise ValueError("retained event count is inconsistent or unbounded")
        if len(self.conflicts) > MAX_CONFLICTS:
            raise ValueError("conflict bound exceeded")

    @property
    def identity(self) -> str:
        return self.snapshot_identity

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_type": self.asset_type.value,
            "canonical_pair": self.canonical_pair,
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "snapshot_schema_version": self.snapshot_schema_version,
            "snapshot_identity": self.snapshot_identity,
            "base_asset_id": self.base_asset_id,
            "quote_asset_id": self.quote_asset_id,
            "project_id": self.project_id,
            "network_id": self.network_id,
            "events": tuple(item.to_dict() for item in self.events),
            "provider_availability": tuple(_availability_dict(item) for item in self.provider_availability),
            "candidate_event_count": self.candidate_event_count,
            "retained_event_count": self.retained_event_count,
            "truncated": self.truncated,
            "conflicts": self.conflicts,
            "research_only": True,
        }


def _availability_dict(item: CryptoCatalystProviderAvailabilityRecord) -> dict[str, object]:
    return {
        "provider_id": item.provider_id,
        "availability_state": item.availability_state.value,
        "source_tier": item.source_tier.value,
        "last_success_at": item.last_success_at.isoformat() if item.last_success_at else None,
        "last_attempt_at": item.last_attempt_at.isoformat() if item.last_attempt_at else None,
        "failure_category": item.failure_category,
        "evidence_cutoff": item.evidence_cutoff.isoformat() if item.evidence_cutoff else None,
    }


def _relevance(evidence: CryptoCatalystEvidence, pair: CryptoPairIdentity) -> CryptoCatalystPairRelevance:
    if evidence.association_confidence is CryptoAssociationConfidence.AMBIGUOUS:
        return CryptoCatalystPairRelevance.AMBIGUOUS
    if evidence.association_confidence is CryptoAssociationConfidence.UNRESOLVED:
        return CryptoCatalystPairRelevance.UNRESOLVED
    pairs = {item.pair.canonical_pair for item in evidence.associated_pairs}
    if pair.canonical_pair in pairs:
        return CryptoCatalystPairRelevance.DIRECT_PAIR
    if evidence.association_confidence is CryptoAssociationConfidence.MULTI_ASSET:
        return CryptoCatalystPairRelevance.MULTI_ASSET
    if evidence.project is not None:
        return CryptoCatalystPairRelevance.PROJECT
    if evidence.token_symbol and evidence.token_symbol in {pair.base_token_symbol, pair.quote_token_symbol}:
        return CryptoCatalystPairRelevance.BASE_ASSET if evidence.token_symbol == pair.base_token_symbol else CryptoCatalystPairRelevance.QUOTE_ASSET
    if evidence.association_confidence is CryptoAssociationConfidence.PROVIDER_MAPPED:
        return CryptoCatalystPairRelevance.NETWORK
    return CryptoCatalystPairRelevance.UNRELATED


def _quality(
    evidence: CryptoCatalystEvidence,
    relevance: CryptoCatalystPairRelevance,
    freshness: CryptoFreshness,
    tier: CryptoCatalystProviderTier,
    conflict_reasons: tuple[str, ...] = (),
) -> tuple[CryptoCatalystQualityState, tuple[str, ...]]:
    reasons: list[str] = []
    if not evidence.event_identity or not evidence.revision_identity:
        reasons.append("missing_stable_identity")
    if not evidence.provider_id or not evidence.source_reference:
        reasons.append("missing_provenance")
    if evidence.published_at is None or evidence.observed_at is None:
        reasons.append("missing_timestamps")
    if relevance is CryptoCatalystPairRelevance.AMBIGUOUS:
        reasons.append("ambiguous_relevance")
    elif relevance is CryptoCatalystPairRelevance.UNRESOLVED:
        reasons.append("unresolved_relevance")
    if tier in {CryptoCatalystProviderTier.UNVERIFIED, CryptoCatalystProviderTier.UNAVAILABLE}:
        reasons.append("weak_provider_tier")
    if freshness is CryptoFreshness.STALE:
        reasons.append("stale_at_cutoff")
    if relevance is CryptoCatalystPairRelevance.UNRELATED:
        reasons.append("not_relevant_to_pair")
    reasons.extend(conflict_reasons)
    if conflict_reasons:
        return CryptoCatalystQualityState.CONFLICTING, tuple(reasons[:MAX_QUALITY_REASONS])
    if reasons and "stale_at_cutoff" in reasons and len(reasons) == 1:
        return CryptoCatalystQualityState.STALE, tuple(reasons)
    if relevance is CryptoCatalystPairRelevance.AMBIGUOUS:
        return CryptoCatalystQualityState.AMBIGUOUS, tuple(reasons[:MAX_QUALITY_REASONS])
    if relevance is CryptoCatalystPairRelevance.UNRESOLVED:
        return CryptoCatalystQualityState.UNRESOLVED, tuple(reasons[:MAX_QUALITY_REASONS])
    if "missing_provenance" in reasons:
        return CryptoCatalystQualityState.INSUFFICIENT_PROVENANCE, tuple(reasons[:MAX_QUALITY_REASONS])
    if "weak_provider_tier" in reasons:
        return CryptoCatalystQualityState.QUALIFIED_WITH_LIMITATIONS, tuple(reasons[:MAX_QUALITY_REASONS])
    if freshness is CryptoFreshness.STALE:
        return CryptoCatalystQualityState.STALE, tuple(reasons[:MAX_QUALITY_REASONS])
    if reasons:
        return CryptoCatalystQualityState.QUALIFIED_WITH_LIMITATIONS, tuple(reasons[:MAX_QUALITY_REASONS])
    return CryptoCatalystQualityState.QUALIFIED, ()


def _group_metadata(
    retained: tuple[CryptoCatalystEvidence, ...],
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """Derive corroboration/conflict only inside an existing canonical event key."""
    groups: dict[str, list[CryptoCatalystEvidence]] = {}
    for item in retained:
        groups.setdefault(item.event_identity, []).append(item)
    provider_counts: dict[str, int] = {}
    conflicts: dict[str, tuple[str, ...]] = {}
    for identity in sorted(groups):
        values = groups[identity]
        provider_counts[identity] = len({item.provider_id for item in values})
        providers = {item.provider_id for item in values}
        if len(providers) < 2:
            continue
        reasons: list[str] = []
        if len({item.status for item in values}) > 1:
            reasons.append("STATUS_CONFLICT")
        if len({item.effective_at for item in values}) > 1:
            reasons.append("EFFECTIVE_TIME_CONFLICT")
        identities = {
            (
                item.project.project_id if item.project else None,
                item.token_symbol,
                tuple(sorted(association.pair.canonical_pair for association in item.associated_pairs)),
                item.association_confidence,
            )
            for item in values
        }
        if len(identities) > 1:
            reasons.append("IDENTITY_CONFLICT")
        if reasons:
            conflicts[identity] = tuple(sorted(set(reasons))[:MAX_QUALITY_REASONS])
    return provider_counts, conflicts


def _overall(events: tuple[CryptoCatalystDecisionEvent, ...], availability: tuple[CryptoCatalystProviderAvailabilityRecord, ...], truncated: bool, conflicts: tuple[str, ...]) -> CryptoCatalystOverallState:
    if conflicts:
        return CryptoCatalystOverallState.CONFLICTING_EVIDENCE
    if not events:
        if any(item.availability_state in {CryptoCatalystProviderAvailability.BLOCKED, CryptoCatalystProviderAvailability.FAILED} for item in availability):
            return CryptoCatalystOverallState.UNAVAILABLE
        return CryptoCatalystOverallState.NO_EVIDENCE
    states = {item.quality_state for item in events}
    if states <= {CryptoCatalystQualityState.STALE}:
        return CryptoCatalystOverallState.STALE_ONLY
    if any(item in states for item in {CryptoCatalystQualityState.AMBIGUOUS, CryptoCatalystQualityState.UNRESOLVED}):
        return CryptoCatalystOverallState.AMBIGUOUS_EVIDENCE
    if truncated or any(item is not CryptoCatalystQualityState.QUALIFIED for item in states):
        return CryptoCatalystOverallState.LIMITED_EVIDENCE
    return CryptoCatalystOverallState.QUALIFIED_EVIDENCE


def build_crypto_catalyst_snapshot(
    *,
    pair: CryptoPairIdentity,
    decision_cutoff: datetime,
    evidence: Iterable[CryptoCatalystEvidence],
    provider_tiers: Mapping[str, CryptoCatalystProviderTier] | None = None,
    source_precisions: Mapping[str, str] | None = None,
    provider_availability: Iterable[CryptoCatalystProviderAvailabilityRecord] = (),
    conflicts: Iterable[str] = (),
    maximum_candidate_events: int = MAX_CANDIDATE_EVENTS,
    maximum_retained_events: int = MAX_RETAINED_EVENTS,
) -> CryptoCatalystDecisionSnapshot:
    """Build a deterministic immutable snapshot from already-normalized evidence."""
    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    cutoff = decision_cutoff.astimezone(UTC)
    if not 1 <= maximum_candidate_events <= MAX_CANDIDATE_EVENTS or not 1 <= maximum_retained_events <= MAX_RETAINED_EVENTS:
        raise ValueError("snapshot bounds are invalid")
    all_candidates = tuple(evidence)
    original_count = len(all_candidates)
    candidates = all_candidates
    if len(candidates) > maximum_candidate_events:
        candidates = candidates[:maximum_candidate_events]
    eligible = tuple(item for item in candidates if item.eligible_at(cutoff))
    ordered = sorted(eligible, key=lambda item: (item.published_at, item.provider_id, item.event_identity, item.revision_identity))
    truncated = len(ordered) > maximum_retained_events or original_count > maximum_candidate_events
    retained = ordered[-maximum_retained_events:]
    tiers = provider_tiers or {}
    precisions = source_precisions or {}
    availability = tuple(sorted(provider_availability, key=lambda item: item.provider_id))
    conflict_values = tuple(sorted(set(str(item) for item in conflicts)))[:MAX_CONFLICTS]
    provider_counts, derived_conflicts = _group_metadata(retained)
    for reasons in derived_conflicts.values():
        conflict_values = tuple(sorted(set((*conflict_values, *reasons))))[:MAX_CONFLICTS]
    events: list[CryptoCatalystDecisionEvent] = []
    for item in retained:
        freshness = item.freshness_at(cutoff)
        relevance = _relevance(item, pair)
        tier = tiers.get(item.provider_id, CryptoCatalystProviderTier.UNVERIFIED)
        event_conflicts = derived_conflicts.get(item.event_identity, ())
        quality, reasons = _quality(item, relevance, freshness, tier, event_conflicts)
        age = max(0, int((cutoff - item.published_at).total_seconds()))
        network_id = item.project.network if item.project else None
        events.append(CryptoCatalystDecisionEvent(
            event_identity=item.event_identity, revision_identity=item.revision_identity,
            event_type=item.event_type.value, direction=item.expected_direction.value, status=item.status.value,
            project_id=item.project.project_id if item.project else None, token_symbol=item.token_symbol,
            network_id=network_id, canonical_pairs=tuple(sorted({assoc.pair.canonical_pair for assoc in item.associated_pairs})),
            relevance=relevance, association_state=item.association_confidence, published_at=item.published_at,
            effective_at=item.effective_at, observed_at=item.observed_at, age_seconds=age, freshness=freshness,
            freshness_policy_version="crypto-freshness-v1", provider_id=item.provider_id, provider_tier=tier,
            source_precision=precisions.get(item.provider_id, "TIMESTAMP"), source_reference=item.source_reference,
            corroborated=provider_counts.get(item.event_identity, 1) >= 2,
            independent_provider_count=provider_counts.get(item.event_identity, 1),
            revision_number=item.revision, supersedes=item.supersedes,
            evidence_complete=not reasons, quality_state=quality, quality_reasons=reasons,
        ))
    immutable_events = tuple(events)
    categories = tuple(sorted({item.event_type for item in immutable_events}))
    qualified = [item for item in immutable_events if item.quality_state is CryptoCatalystQualityState.QUALIFIED]
    states = [item.quality_state for item in immutable_events]
    tier_order = {
        CryptoCatalystProviderTier.UNAVAILABLE: 0,
        CryptoCatalystProviderTier.UNVERIFIED: 1,
        CryptoCatalystProviderTier.CORROBORATION_ONLY: 2,
        CryptoCatalystProviderTier.SECONDARY_VERIFIED: 3,
        CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY: 4,
        CryptoCatalystProviderTier.OFFICIAL_REGULATORY: 5,
        CryptoCatalystProviderTier.AUTHORITATIVE_PRIMARY: 6,
    }
    present_tiers = [item.provider_tier for item in immutable_events]
    highest = max(present_tiers, key=lambda value: tier_order[value]) if present_tiers else None
    summary = CryptoCatalystSnapshotSummary(
        eligible_event_count=len(eligible), qualified_event_count=len(qualified),
        limited_event_count=sum(item.quality_state is CryptoCatalystQualityState.QUALIFIED_WITH_LIMITATIONS for item in immutable_events),
        stale_event_count=sum(item.quality_state is CryptoCatalystQualityState.STALE for item in immutable_events),
        ambiguous_event_count=sum(item.quality_state is CryptoCatalystQualityState.AMBIGUOUS for item in immutable_events),
        unresolved_event_count=sum(item.quality_state is CryptoCatalystQualityState.UNRESOLVED for item in immutable_events),
        conflicting_event_count=sum(item.quality_state is CryptoCatalystQualityState.CONFLICTING for item in immutable_events),
        fresh_event_count=sum(item.freshness is CryptoFreshness.FRESH for item in immutable_events),
        independent_provider_count=len({item.provider_id for item in immutable_events}), event_categories_present=categories,
        latest_qualified_event_age=min((item.age_seconds for item in qualified), default=None), highest_provider_tier=highest,
        has_network_incident=any(item.event_type in {"NETWORK_OUTAGE", "NETWORK_RECOVERY", "NETWORK_CONGESTION"} for item in immutable_events),
        has_regulatory_event=any(item.event_type.startswith("REGULATORY") or item.event_type.startswith("ETF_") for item in immutable_events),
        has_exchange_event=any(item.event_type.startswith("EXCHANGE_") for item in immutable_events),
        has_security_event=any(item.event_type.startswith("SECURITY_") for item in immutable_events),
        overall_evidence_state=_overall(immutable_events, availability, truncated, conflict_values),
    )
    semantic = {
        "schema": SNAPSHOT_SCHEMA_VERSION, "asset_type": AssetType.CRYPTO.value, "pair": pair.canonical_pair,
        "base_asset_id": pair.base_asset_id, "quote_asset_id": pair.quote_asset_id, "cutoff": cutoff.isoformat(),
        "events": tuple(item.to_dict() for item in immutable_events), "availability": tuple(_availability_dict(item) for item in availability),
        "candidate_count": len(candidates), "truncated": truncated, "conflicts": conflict_values,
    }
    identity = semantic_digest("crypto-catalyst-snapshot", semantic)
    return CryptoCatalystDecisionSnapshot(
        asset_type=AssetType.CRYPTO, canonical_pair=pair.canonical_pair, decision_cutoff=cutoff,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION, snapshot_identity=identity,
        base_asset_id=pair.base_asset_id, quote_asset_id=pair.quote_asset_id,
        project_id=(next(iter({item.project_id for item in immutable_events if item.project_id}), None)
                    if len({item.project_id for item in immutable_events if item.project_id}) <= 1 else None),
        network_id=(next(iter({item.network_id for item in immutable_events if item.network_id}), None)
                    if len({item.network_id for item in immutable_events if item.network_id}) <= 1 else None), events=immutable_events, summary=summary,
        provider_availability=availability, candidate_event_count=len(candidates), retained_event_count=len(immutable_events),
        truncated=truncated, conflicts=conflict_values,
    )


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION", "MAX_CANDIDATE_EVENTS", "MAX_RETAINED_EVENTS", "MAX_QUALITY_REASONS", "MAX_CONFLICTS",
    "CryptoCatalystPairRelevance", "CryptoCatalystProviderTier", "CryptoCatalystQualityState", "CryptoCatalystOverallState",
    "CryptoCatalystProviderAvailability", "CryptoCatalystProviderAvailabilityRecord", "CryptoCatalystDecisionEvent",
    "CryptoCatalystSnapshotSummary", "CryptoCatalystDecisionSnapshot", "build_crypto_catalyst_snapshot",
]
