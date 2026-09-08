"""Provider-neutral, research-only crypto catalyst identity and evidence contracts."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.assets import AssetType
from app.research_core import EvidenceProvenance, semantic_digest


SCHEMA_VERSION = "crypto-catalyst-v1"
MAX_PROJECTS = 512
MAX_EVENTS_PER_PROJECT = 32
MAX_CANONICAL_EVENTS = 4096
MAX_REVISION_IDENTITIES = 8192


class CryptoCatalystType(StrEnum):
    EXCHANGE_LISTING = "EXCHANGE_LISTING"
    EXCHANGE_DELISTING = "EXCHANGE_DELISTING"
    PROTOCOL_UPGRADE = "PROTOCOL_UPGRADE"
    HARD_FORK = "HARD_FORK"
    TOKEN_UNLOCK = "TOKEN_UNLOCK"
    EMISSIONS_CHANGE = "EMISSIONS_CHANGE"
    SECURITY_EXPLOIT = "SECURITY_EXPLOIT"
    SECURITY_PATCH = "SECURITY_PATCH"
    NETWORK_OUTAGE = "NETWORK_OUTAGE"
    NETWORK_RECOVERY = "NETWORK_RECOVERY"
    NETWORK_CONGESTION = "NETWORK_CONGESTION"
    GOVERNANCE_PROPOSAL = "GOVERNANCE_PROPOSAL"
    GOVERNANCE_VOTE = "GOVERNANCE_VOTE"
    GOVERNANCE_RESULT = "GOVERNANCE_RESULT"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    REGULATORY_APPROVAL = "REGULATORY_APPROVAL"
    REGULATORY_RESTRICTION = "REGULATORY_RESTRICTION"
    ETF_FILING = "ETF_FILING"
    ETF_APPROVAL = "ETF_APPROVAL"
    ETF_REJECTION = "ETF_REJECTION"
    INSTITUTIONAL_FLOW_EVENT = "INSTITUTIONAL_FLOW_EVENT"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"
    STABLECOIN_RECOVERY = "STABLECOIN_RECOVERY"
    PARTNERSHIP = "PARTNERSHIP"
    ECOSYSTEM_ANNOUNCEMENT = "ECOSYSTEM_ANNOUNCEMENT"
    TREASURY_EVENT = "TREASURY_EVENT"
    FUNDING_EVENT = "FUNDING_EVENT"
    MACRO_EVENT = "MACRO_EVENT"
    PROJECT_ANNOUNCEMENT = "PROJECT_ANNOUNCEMENT"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


class CryptoCatalystDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class CryptoCatalystStatus(StrEnum):
    ANNOUNCED = "ANNOUNCED"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DELAYED = "DELAYED"
    RESOLVED = "RESOLVED"
    REVISED = "REVISED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class CryptoAssociationConfidence(StrEnum):
    EXACT = "EXACT"
    VERIFIED_ALIAS = "VERIFIED_ALIAS"
    PROVIDER_MAPPED = "PROVIDER_MAPPED"
    MULTI_ASSET = "MULTI_ASSET"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class CryptoAssociationType(StrEnum):
    PROJECT = "PROJECT"
    TOKEN = "TOKEN"
    NETWORK = "NETWORK"
    PAIR = "PAIR"
    EXCHANGE = "EXCHANGE"


class CryptoFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


def _text(value: object, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _aware(value: datetime | None, name: str, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _symbols(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip().upper() for value in values if str(value).strip()}))


@dataclass(frozen=True, slots=True)
class CryptoProjectIdentity:
    project_id: str
    canonical_project_name: str
    canonical_token_symbol: str | None = None
    canonical_token_name: str | None = None
    aliases: tuple[str, ...] = ()
    historical_symbols: tuple[str, ...] = ()
    network: str | None = None
    provider_references: tuple[tuple[str, str], ...] = ()
    identity_version: str = "crypto-project-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _text(self.project_id, "project_id"))
        object.__setattr__(self, "canonical_project_name", _text(self.canonical_project_name, "canonical_project_name"))
        object.__setattr__(self, "canonical_token_symbol", self.canonical_token_symbol.strip().upper() if self.canonical_token_symbol else None)
        object.__setattr__(self, "canonical_token_name", self.canonical_token_name.strip() if self.canonical_token_name else None)
        object.__setattr__(self, "aliases", _symbols(self.aliases))
        object.__setattr__(self, "historical_symbols", _symbols(self.historical_symbols))
        object.__setattr__(self, "network", self.network.strip() if self.network else None)
        refs = tuple(sorted((str(key).strip(), str(value).strip()) for key, value in self.provider_references if str(key).strip() and str(value).strip()))
        object.__setattr__(self, "provider_references", refs)
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))

    @property
    def identity(self) -> str:
        return semantic_digest("crypto-project", self.project_id, self.identity_version)


@dataclass(frozen=True, slots=True)
class CryptoPairIdentity:
    canonical_pair: str
    provider_symbol: str
    instrument_id: str | None = None
    base_token_symbol: str | None = None
    quote_token_symbol: str | None = None

    def __post_init__(self) -> None:
        pair = _text(self.canonical_pair, "canonical_pair").upper()
        if pair.count("/") != 1 or any(not part for part in pair.split("/")):
            raise ValueError("canonical_pair must be BASE/QUOTE")
        object.__setattr__(self, "canonical_pair", pair)
        object.__setattr__(self, "provider_symbol", _text(self.provider_symbol, "provider_symbol").upper())
        object.__setattr__(self, "instrument_id", self.instrument_id.strip() if self.instrument_id else None)
        base, quote = pair.split("/")
        object.__setattr__(self, "base_token_symbol", (self.base_token_symbol or base).strip().upper())
        object.__setattr__(self, "quote_token_symbol", (self.quote_token_symbol or quote).strip().upper())


@dataclass(frozen=True, slots=True)
class CryptoPairAssociation:
    pair: CryptoPairIdentity
    association_type: CryptoAssociationType
    confidence: CryptoAssociationConfidence
    reason: str
    identity_version: str = "crypto-association-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "association reason"))
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))


@dataclass(frozen=True, slots=True)
class CryptoIdentityLookup:
    query: str
    confidence: CryptoAssociationConfidence
    matches: tuple[CryptoProjectIdentity, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.confidence in {CryptoAssociationConfidence.AMBIGUOUS, CryptoAssociationConfidence.UNRESOLVED}


class CryptoProjectRegistry:
    """Bounded immutable project registry; collisions never silently resolve."""

    def __init__(self, identities: Iterable[CryptoProjectIdentity] = (), *, maximum_projects: int = MAX_PROJECTS) -> None:
        if maximum_projects <= 0:
            raise ValueError("maximum_projects must be positive")
        supplied = tuple(identities)
        if len(supplied) > maximum_projects:
            raise ValueError("project registry bound exceeded")
        if len({item.project_id for item in supplied}) != len(supplied):
            raise ValueError("project_id values must be unique")
        self.maximum_projects = maximum_projects
        self._identities = tuple(sorted(supplied, key=lambda item: item.project_id))

    @property
    def identities(self) -> tuple[CryptoProjectIdentity, ...]:
        return self._identities

    def lookup(self, value: str) -> CryptoIdentityLookup:
        query = str(value).strip().upper()
        matches = tuple(item for item in self._identities if query in {item.project_id.upper(), item.canonical_token_symbol or "", *item.aliases, *item.historical_symbols})
        if not matches:
            return CryptoIdentityLookup(query, CryptoAssociationConfidence.UNRESOLVED)
        if len(matches) > 1:
            return CryptoIdentityLookup(query, CryptoAssociationConfidence.AMBIGUOUS, matches)
        item = matches[0]
        confidence = CryptoAssociationConfidence.EXACT if query == item.project_id.upper() else CryptoAssociationConfidence.VERIFIED_ALIAS
        return CryptoIdentityLookup(query, confidence, (item,))

    def add(self, identity: CryptoProjectIdentity) -> "CryptoProjectRegistry":
        return CryptoProjectRegistry((*self._identities, identity), maximum_projects=self.maximum_projects)


def bootstrap_pair_identity(canonical_pair: str, provider_symbol: str, instrument_id: str | None = None) -> CryptoPairIdentity:
    """Bootstrap pair metadata from Webull discovery without claiming project identity."""

    return CryptoPairIdentity(canonical_pair, provider_symbol, instrument_id)


@dataclass(frozen=True, slots=True)
class CryptoCatalystEvidence:
    event_type: CryptoCatalystType
    status: CryptoCatalystStatus
    provider_id: str
    source_name: str
    source_reference: str
    published_at: datetime
    observed_at: datetime
    effective_at: datetime | None = None
    project: CryptoProjectIdentity | None = None
    token_symbol: str | None = None
    associated_pairs: tuple[CryptoPairAssociation, ...] = ()
    association_confidence: CryptoAssociationConfidence = CryptoAssociationConfidence.UNRESOLVED
    expected_direction: CryptoCatalystDirection = CryptoCatalystDirection.UNKNOWN
    title: str = ""
    summary: str | None = None
    source_url: str | None = None
    provider_event_id: str | None = None
    underlying_event_key: str | None = None
    revision: int = 1
    supersedes: str | None = None
    provenance: EvidenceProvenance | None = None
    decision_cutoff: datetime | None = None
    freshness_policy_seconds: int | None = None
    schema_version: str = SCHEMA_VERSION
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("crypto catalyst evidence must remain research-only")
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id"))
        object.__setattr__(self, "source_name", _text(self.source_name, "source_name"))
        object.__setattr__(self, "source_reference", _text(self.source_reference, "source_reference"))
        object.__setattr__(self, "title", str(self.title).strip())
        object.__setattr__(self, "summary", self.summary.strip() if self.summary else None)
        object.__setattr__(self, "source_url", self.source_url.strip() if self.source_url else None)
        object.__setattr__(self, "token_symbol", self.token_symbol.strip().upper() if self.token_symbol else None)
        object.__setattr__(self, "provider_event_id", self.provider_event_id.strip() if self.provider_event_id else None)
        object.__setattr__(self, "underlying_event_key", self.underlying_event_key.strip() if self.underlying_event_key else None)
        object.__setattr__(self, "published_at", _aware(self.published_at, "published_at", required=True))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at", required=True))
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at"))
        cutoff = _aware(self.decision_cutoff, "decision_cutoff")
        object.__setattr__(self, "decision_cutoff", cutoff)
        if self.observed_at < self.published_at:
            raise ValueError("observed_at cannot precede published_at")
        if self.revision <= 0:
            raise ValueError("revision must be positive")
        if self.freshness_policy_seconds is not None and self.freshness_policy_seconds <= 0:
            raise ValueError("freshness policy must be positive")
        if self.provenance is not None and self.provenance.observed_at > self.observed_at:
            raise ValueError("provenance cannot be observed after evidence")
        if cutoff is not None and not self.eligible_at(cutoff):
            raise ValueError("evidence is not eligible at its decision_cutoff")

    @property
    def event_identity(self) -> str:
        stable_key = self.underlying_event_key or self.provider_event_id or self.title.casefold()
        project_key = self.project.project_id if self.project else (self.token_symbol or "UNRESOLVED")
        effective = self.effective_at.isoformat() if self.effective_at else ""
        return semantic_digest("crypto-event", project_key, self.event_type.value, stable_key, self.published_at.isoformat(), effective)

    @property
    def revision_identity(self) -> str:
        return semantic_digest("crypto-event-revision", self.event_identity, self.revision, self.status.value, self.effective_at, self.association_confidence.value)

    @property
    def evidence_identity(self) -> str:
        # A provider's syndicated copies are one observation even when URLs differ;
        # independent providers remain distinct corroborating evidence.
        return semantic_digest("crypto-evidence", self.event_identity, self.revision_identity, self.provider_id)

    def eligible_at(self, cutoff: datetime) -> bool:
        boundary = _aware(cutoff, "decision_cutoff", required=True)
        return self.published_at <= boundary and self.observed_at <= boundary

    def freshness_at(self, cutoff: datetime) -> CryptoFreshness:
        boundary = _aware(cutoff, "decision_cutoff", required=True)
        if not self.eligible_at(boundary):
            return CryptoFreshness.UNAVAILABLE
        if self.freshness_policy_seconds is None:
            return CryptoFreshness.FRESH
        age = (boundary - self.published_at).total_seconds()
        return CryptoFreshness.FRESH if age <= self.freshness_policy_seconds else CryptoFreshness.STALE

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "event_identity": self.event_identity,
            "revision_identity": self.revision_identity, "event_type": self.event_type.value,
            "status": self.status.value, "provider_id": self.provider_id, "source_name": self.source_name,
            "source_reference": self.source_reference, "source_url": self.source_url,
            "provider_event_id": self.provider_event_id, "project_id": self.project.project_id if self.project else None,
            "token_symbol": self.token_symbol, "associated_pairs": [item.pair.canonical_pair for item in self.associated_pairs],
            "association_confidence": self.association_confidence.value, "expected_direction": self.expected_direction.value,
            "title": self.title, "summary": self.summary, "published_at": self.published_at.isoformat(),
            "effective_at": self.effective_at.isoformat() if self.effective_at else None, "observed_at": self.observed_at.isoformat(),
            "revision": self.revision, "supersedes": self.supersedes, "research_only": True,
            "production_promoted": False, "selection_authorized": False, "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class CryptoCatalystEvent:
    identity: str
    revisions: tuple[CryptoCatalystEvidence, ...]

    @property
    def latest(self) -> CryptoCatalystEvidence:
        return max(self.revisions, key=lambda item: (item.revision, item.observed_at, item.revision_identity))

    @property
    def source_count(self) -> int:
        return len({item.source_reference for item in self.revisions})

    @property
    def independent_provider_count(self) -> int:
        return len({item.provider_id for item in self.revisions})

    @property
    def corroborated(self) -> bool:
        return self.independent_provider_count >= 2


@dataclass(frozen=True, slots=True)
class CryptoCatalystMetrics:
    crypto_catalyst_projects: int
    crypto_catalyst_events_retained: int
    crypto_catalyst_event_high_water: int
    crypto_catalyst_events_evicted: int
    crypto_catalyst_ambiguous: int
    crypto_catalyst_unresolved: int
    crypto_catalyst_corroborated: int
    crypto_catalyst_revisions: int
    crypto_catalyst_provider_failures: int
    crypto_catalyst_duplicates_suppressed: int


@dataclass(frozen=True, slots=True)
class CryptoCatalystAggregation:
    events: tuple[CryptoCatalystEvent, ...]
    rejected_future: int
    material_changes: tuple[str, ...]
    metrics: CryptoCatalystMetrics


@dataclass(frozen=True, slots=True)
class CryptoCatalystSummary:
    events_by_type: tuple[tuple[str, int], ...]
    events_by_project: tuple[tuple[str, int], ...]
    fresh_count: int
    stale_count: int
    ambiguous_count: int
    unresolved_count: int
    corroborated_count: int
    revision_count: int
    provider_failures: int


@runtime_checkable
class CryptoCatalystProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def collect(self, as_of: datetime) -> Iterable[CryptoCatalystEvidence]: ...


class CryptoCatalystAggregator:
    """Bounded provider-neutral aggregation; no network or execution behavior."""

    def __init__(self, *, maximum_events_per_project: int = MAX_EVENTS_PER_PROJECT, maximum_canonical_events: int = MAX_CANONICAL_EVENTS, maximum_revision_identities: int = MAX_REVISION_IDENTITIES) -> None:
        if min(maximum_events_per_project, maximum_canonical_events, maximum_revision_identities) <= 0:
            raise ValueError("catalyst bounds must be positive")
        self.maximum_events_per_project = maximum_events_per_project
        self.maximum_canonical_events = maximum_canonical_events
        self.maximum_revision_identities = maximum_revision_identities
        self._events: OrderedDict[str, list[CryptoCatalystEvidence]] = OrderedDict()
        self._revisions: OrderedDict[str, None] = OrderedDict()
        self._signatures: OrderedDict[str, None] = OrderedDict()
        self._evicted = 0
        self._high_water = 0
        self._failures = 0
        self._duplicates = 0

    def aggregate(self, evidence: Iterable[CryptoCatalystEvidence], *, decision_cutoff: datetime) -> CryptoCatalystAggregation:
        cutoff = _aware(decision_cutoff, "decision_cutoff", required=True)
        rejected = 0
        changes: list[str] = []
        for item in evidence:
            try:
                if not item.eligible_at(cutoff):
                    rejected += 1
                    continue
                if item.evidence_identity in self._revisions:
                    self._duplicates += 1
                    continue
                self._revisions[item.evidence_identity] = None
                self._revisions.move_to_end(item.evidence_identity)
                bucket = self._events.setdefault(item.event_identity, [])
                if not bucket:
                    changes.append(item.event_identity)
                elif item.revision_identity not in {existing.revision_identity for existing in bucket}:
                    changes.append(item.revision_identity)
                bucket.append(item)
                bucket.sort(key=lambda value: (value.revision, value.observed_at, value.revision_identity))
                self._events.move_to_end(item.event_identity)
                project_key = item.project.project_id if item.project is not None else "UNRESOLVED"
                project_events = [
                    identity for identity, values in self._events.items()
                    if (values[0].project.project_id if values and values[0].project is not None else "UNRESOLVED") == project_key
                ]
                while len(project_events) > self.maximum_events_per_project:
                    oldest = project_events.pop(0)
                    self._events.pop(oldest, None)
                    self._evicted += 1
                signature = semantic_digest(item.event_identity, item.revision_identity, item.association_confidence.value, item.status.value)
                if signature not in self._signatures:
                    self._signatures[signature] = None
                while len(self._revisions) > self.maximum_revision_identities:
                    oldest_revision, _ = self._revisions.popitem(last=False)
                    for event_identity, revisions in tuple(self._events.items()):
                        remaining = [item for item in revisions if item.evidence_identity != oldest_revision]
                        if len(remaining) != len(revisions):
                            if remaining:
                                self._events[event_identity] = remaining
                            else:
                                self._events.pop(event_identity, None)
                            break
                while len(self._events) > self.maximum_canonical_events:
                    self._events.popitem(last=False)
                    self._evicted += 1
                while len(self._signatures) > self.maximum_revision_identities:
                    self._signatures.popitem(last=False)
            except Exception:
                self._failures += 1
        self._high_water = max(self._high_water, len(self._events))
        events = tuple(CryptoCatalystEvent(identity, tuple(values)) for identity, values in self._events.items())
        metrics = self.metrics()
        return CryptoCatalystAggregation(events, rejected, tuple(changes), metrics)

    def aggregate_provider(self, provider: CryptoCatalystProvider, *, decision_cutoff: datetime) -> CryptoCatalystAggregation:
        try:
            evidence = provider.collect(decision_cutoff)
        except Exception:
            self._failures += 1
            evidence = ()
        return self.aggregate(evidence, decision_cutoff=decision_cutoff)

    def metrics(self) -> CryptoCatalystMetrics:
        projects = {item.project.project_id for values in self._events.values() for item in values if item.project is not None}
        ambiguous = sum(1 for values in self._events.values() for item in values if item.association_confidence is CryptoAssociationConfidence.AMBIGUOUS)
        unresolved = sum(1 for values in self._events.values() for item in values if item.association_confidence is CryptoAssociationConfidence.UNRESOLVED)
        corroborated = sum(1 for values in self._events.values() if len({item.provider_id for item in values}) >= 2)
        revisions = sum(max(0, len(values) - 1) for values in self._events.values())
        return CryptoCatalystMetrics(len(projects), len(self._events), self._high_water, self._evicted, ambiguous, unresolved, corroborated, revisions, self._failures, self._duplicates)


def material_event_signature(event: CryptoCatalystEvidence) -> str:
    """Signature for meaningful state changes, excluding retrieval time noise."""

    return semantic_digest(event.event_identity, event.revision_identity, event.status.value, event.effective_at, event.association_confidence.value)


def summarize_crypto_catalysts(aggregation: CryptoCatalystAggregation, *, decision_cutoff: datetime) -> CryptoCatalystSummary:
    """Return bounded, read-only introspection over an explicit aggregation."""

    cutoff = _aware(decision_cutoff, "decision_cutoff", required=True)
    by_type: dict[str, int] = {}
    by_project: dict[str, int] = {}
    fresh = stale = ambiguous = unresolved = corroborated = revisions = 0
    for event in aggregation.events:
        latest = event.latest
        by_type[latest.event_type.value] = by_type.get(latest.event_type.value, 0) + 1
        project_id = latest.project.project_id if latest.project is not None else "UNRESOLVED"
        by_project[project_id] = by_project.get(project_id, 0) + 1
        freshness = latest.freshness_at(cutoff)
        fresh += freshness is CryptoFreshness.FRESH
        stale += freshness is CryptoFreshness.STALE
        ambiguous += latest.association_confidence is CryptoAssociationConfidence.AMBIGUOUS
        unresolved += latest.association_confidence is CryptoAssociationConfidence.UNRESOLVED
        corroborated += event.corroborated
        revisions += max(0, len(event.revisions) - 1)
    return CryptoCatalystSummary(tuple(sorted(by_type.items())), tuple(sorted(by_project.items())), fresh, stale, ambiguous, unresolved, corroborated, revisions, aggregation.metrics.crypto_catalyst_provider_failures)


__all__ = [
    "SCHEMA_VERSION", "MAX_PROJECTS", "MAX_EVENTS_PER_PROJECT", "MAX_CANONICAL_EVENTS", "MAX_REVISION_IDENTITIES",
    "CryptoCatalystType", "CryptoCatalystDirection", "CryptoCatalystStatus", "CryptoAssociationConfidence", "CryptoAssociationType", "CryptoFreshness",
    "CryptoProjectIdentity", "CryptoPairIdentity", "CryptoPairAssociation", "CryptoIdentityLookup", "CryptoProjectRegistry", "bootstrap_pair_identity",
    "CryptoCatalystEvidence", "CryptoCatalystEvent", "CryptoCatalystMetrics", "CryptoCatalystAggregation", "CryptoCatalystSummary", "CryptoCatalystProvider", "CryptoCatalystAggregator", "material_event_signature", "summarize_crypto_catalysts",
]
