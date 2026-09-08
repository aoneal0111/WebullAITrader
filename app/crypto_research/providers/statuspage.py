"""Bounded, read-only adapters for explicitly verified public Statuspage sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import time
from threading import Lock
from typing import Mapping, Protocol

import httpx

from app.crypto_research.catalysts import (
    CryptoAssociationConfidence,
    CryptoAssociationType,
    CryptoAssetIdentityRegistry,
    CryptoCatalystDirection,
    CryptoCatalystEvidence,
    CryptoCatalystStatus,
    CryptoCatalystType,
    CryptoPairAssociation,
    CryptoProjectIdentity,
)
from app.research_core import EvidenceProvenance


STATUSPAGE_PROVIDER_ID = "STATUSPAGE"
STATUSPAGE_NORMALIZATION_VERSION = "statuspage-incident-v1"
MAX_SOURCES = 16
MAX_INCIDENTS = 100
MAX_UPDATES = 64
MAX_COMPONENTS = 256
MAX_PAGES = 2
MAX_PAYLOAD_BYTES = 8_000_000


class StatusPageFailureKind(StrEnum):
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_4XX = "PROVIDER_4XX"
    PROVIDER_5XX = "PROVIDER_5XX"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"
    SOURCE_UNVERIFIED = "SOURCE_UNVERIFIED"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class StatusPageProviderError(RuntimeError):
    def __init__(self, kind: StatusPageFailureKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class OfficialStatusSource:
    source_id: str
    canonical_name: str
    base_url: str
    technology: str
    ownership_reference: str
    ownership_verified: bool = False
    project_id: str | None = None
    network_id: str | None = None
    identity_version: str = "status-source-v1"

    def __post_init__(self) -> None:
        for name in ("source_id", "canonical_name", "base_url", "technology", "ownership_reference"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.base_url.startswith("https://") or self.base_url.endswith("/"):
            raise ValueError("status source must use an HTTPS base URL without trailing slash")
        if not self.ownership_verified:
            raise ValueError("status source ownership must be explicitly verified")


@dataclass(frozen=True, slots=True)
class StatusPagePolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    maximum_sources: int = 16
    maximum_incidents_per_source: int = 100
    maximum_updates_per_incident: int = 64
    maximum_components_per_source: int = 256
    maximum_pages: int = 2
    maximum_retries: int = 2
    backoff_seconds: float = 0.5
    poll_seconds: int = 120
    maximum_payload_bytes: int = MAX_PAYLOAD_BYTES

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_sources <= MAX_SOURCES or not 1 <= self.maximum_incidents_per_source <= MAX_INCIDENTS:
            raise ValueError("status source/incident bounds are invalid")
        if not 1 <= self.maximum_updates_per_incident <= MAX_UPDATES or not 1 <= self.maximum_components_per_source <= MAX_COMPONENTS:
            raise ValueError("status update/component bounds are invalid")
        if not 1 <= self.maximum_pages <= MAX_PAGES or self.maximum_retries < 0:
            raise ValueError("status page bounds are invalid")
        if self.timeout_seconds <= 0 or self.backoff_seconds < 0 or self.poll_seconds < 60 or self.maximum_payload_bytes <= 0:
            raise ValueError("status page timing/payload policy is invalid")


@dataclass(frozen=True, slots=True)
class StatusComponentRecord:
    source_id: str
    component_id: str
    name: str
    status: str
    group_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StatusIncidentUpdateRecord:
    source_id: str
    incident_id: str
    update_id: str
    status: str
    body: str | None
    created_at: datetime
    updated_at: datetime | None
    affected_component_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StatusIncidentRecord:
    source_id: str
    incident_id: str
    name: str
    status: str
    impact: str
    created_at: datetime
    updated_at: datetime | None
    started_at: datetime | None
    monitoring_at: datetime | None
    resolved_at: datetime | None
    shortlink: str | None
    affected_component_ids: tuple[str, ...]
    updates: tuple[StatusIncidentUpdateRecord, ...]
    scheduled: bool = False


@dataclass(frozen=True, slots=True)
class StatusPageProviderMetrics:
    statuspage_requests_attempted: int
    statuspage_requests_succeeded: int
    statuspage_requests_failed: int
    statuspage_sources_queried: int
    statuspage_incidents_seen: int
    statuspage_incidents_normalized: int
    statuspage_updates_seen: int
    statuspage_duplicates_suppressed: int
    statuspage_revisions: int
    statuspage_exact_associations: int
    statuspage_multi_asset_associations: int
    statuspage_ambiguous_associations: int
    statuspage_unresolved_associations: int
    statuspage_last_success_age_seconds: float | None


class StatusPageTransport(Protocol):
    def get(self, url: str, *, timeout: float) -> object: ...


class _HttpxStatusPageTransport:
    def __init__(self, policy: StatusPagePolicy) -> None:
        self._maximum_payload_bytes = policy.maximum_payload_bytes
        self._client = httpx.Client(headers={"Accept": "application/json", "User-Agent": "AtlasCryptoResearch/1.0"}, timeout=httpx.Timeout(policy.timeout_seconds), follow_redirects=True)

    def get(self, url: str, *, timeout: float) -> object:
        try:
            response = self._client.get(url, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise StatusPageProviderError(StatusPageFailureKind.TIMEOUT, "status page request timed out") from exc
        except httpx.NetworkError as exc:
            raise StatusPageProviderError(StatusPageFailureKind.NETWORK_ERROR, "status page network request failed") from exc
        status = getattr(response, "status_code", None)
        if status == 429:
            raise StatusPageProviderError(StatusPageFailureKind.RATE_LIMITED, "status page rate limit response", status_code=429)
        if isinstance(status, int) and 400 <= status < 500:
            raise StatusPageProviderError(StatusPageFailureKind.PROVIDER_4XX, f"status page HTTP {status}", status_code=status)
        if isinstance(status, int) and status >= 500:
            raise StatusPageProviderError(StatusPageFailureKind.PROVIDER_5XX, f"status page HTTP {status}", status_code=status)
        content = getattr(response, "content", b"")
        if isinstance(content, bytes) and len(content) > self._maximum_payload_bytes:
            raise StatusPageProviderError(StatusPageFailureKind.MALFORMED_RESPONSE, "status page payload exceeds bound")
        try:
            return response.json()
        except Exception as exc:
            raise StatusPageProviderError(StatusPageFailureKind.MALFORMED_RESPONSE, "status page response is not JSON") from exc


class _RateLimiter:
    def __init__(self) -> None:
        self._last: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last is not None and now - self._last < 1.0:
                time.sleep(1.0 - (now - self._last))
            self._last = time.monotonic()


class StatusPageProvider:
    provider_id = STATUSPAGE_PROVIDER_ID
    source_name = "Official Statuspage"

    def __init__(self, sources: tuple[OfficialStatusSource, ...], policy: StatusPagePolicy = StatusPagePolicy(), *, transport: StatusPageTransport | None = None, identity_registry: CryptoAssetIdentityRegistry | None = None) -> None:
        if len(sources) > policy.maximum_sources:
            raise ValueError("status source bound exceeded")
        self.sources = tuple(sources)
        self.policy = policy
        self._transport = transport or _HttpxStatusPageTransport(policy)
        self._identity_registry = identity_registry
        self._limiter = _RateLimiter()
        self._metrics = {name: 0 for name in StatusPageProviderMetrics.__dataclass_fields__ if name != "statuspage_last_success_age_seconds"}
        self._last_success: datetime | None = None

    @property
    def metrics(self) -> StatusPageProviderMetrics:
        age = None if self._last_success is None else max(0.0, (datetime.now(UTC) - self._last_success).total_seconds())
        return StatusPageProviderMetrics(**self._metrics, statuspage_last_success_age_seconds=age)

    def fetch_since(self, since: datetime, *, observed_at: datetime | None = None) -> tuple[CryptoCatalystEvidence, ...]:
        if not self.policy.enabled:
            return ()
        boundary = _utc(since, "since")
        observed = _utc(observed_at or datetime.now(UTC), "observed_at")
        if observed < boundary:
            raise ValueError("observed_at cannot precede since")
        output: list[CryptoCatalystEvidence] = []
        seen: set[str] = set()
        for source in self.sources[: self.policy.maximum_sources]:
            self._metrics["statuspage_sources_queried"] += 1
            try:
                components = _components_from_payload(source.source_id, self._get(source, "/api/v2/components.json"), self.policy.maximum_components_per_source)
                payload = self._get(source, "/api/v2/incidents.json")
                incidents = _incidents_from_payload(source.source_id, payload, self.policy.maximum_incidents_per_source, self.policy.maximum_updates_per_incident)
            except (StatusPageProviderError, ValueError, TypeError):
                self._metrics["statuspage_requests_failed"] += 1
                continue
            self._metrics["statuspage_incidents_seen"] += len(incidents)
            self._metrics["statuspage_updates_seen"] += sum(len(item.updates) for item in incidents)
            for incident in incidents:
                for update in incident.updates:
                    identity = f"{source.source_id}:{incident.incident_id}:{update.update_id}"
                    if identity in seen:
                        self._metrics["statuspage_duplicates_suppressed"] += 1
                        continue
                    seen.add(identity)
                    if update.created_at < boundary or update.created_at > observed:
                        continue
                    try:
                        evidence = self._to_evidence(source, incident, update, components, observed)
                    except (ValueError, TypeError):
                        self._metrics["statuspage_requests_failed"] += 1
                        continue
                    output.append(evidence)
                    self._metrics["statuspage_incidents_normalized"] += 1
                    if update.update_id != incident.incident_id:
                        self._metrics["statuspage_revisions"] += 1
        return tuple(output)

    def _get(self, source: OfficialStatusSource, suffix: str) -> object:
        if not source.ownership_reference.startswith("https://"):
            raise StatusPageProviderError(StatusPageFailureKind.SOURCE_UNVERIFIED, "status source ownership is not verified")
        url = source.base_url + suffix
        for attempt in range(self.policy.maximum_retries + 1):
            self._metrics["statuspage_requests_attempted"] += 1
            self._limiter.wait()
            try:
                payload = self._transport.get(url, timeout=self.policy.timeout_seconds)
                self._metrics["statuspage_requests_succeeded"] += 1
                self._last_success = datetime.now(UTC)
                return payload
            except StatusPageProviderError as exc:
                if exc.kind in {StatusPageFailureKind.TIMEOUT, StatusPageFailureKind.NETWORK_ERROR, StatusPageFailureKind.RATE_LIMITED, StatusPageFailureKind.PROVIDER_5XX} and attempt < self.policy.maximum_retries:
                    if self.policy.backoff_seconds:
                        time.sleep(self.policy.backoff_seconds * (attempt + 1))
                    continue
                raise
        raise StatusPageProviderError(StatusPageFailureKind.NETWORK_ERROR, "status page request exhausted retries")

    def _to_evidence(self, source: OfficialStatusSource, incident: StatusIncidentRecord, update: StatusIncidentUpdateRecord, components: tuple[StatusComponentRecord, ...], observed: datetime) -> CryptoCatalystEvidence:
        component_ids = set(update.affected_component_ids or incident.affected_component_ids)
        component_names = tuple(item.name for item in components if item.component_id in component_ids)
        confidence = CryptoAssociationConfidence.PROVIDER_MAPPED if source.network_id or source.project_id else CryptoAssociationConfidence.UNRESOLVED
        project: CryptoProjectIdentity | None = None
        if source.project_id and self._identity_registry is not None:
            project = self._identity_registry.project_lookup(source.project_id)
            if project is None:
                confidence = CryptoAssociationConfidence.UNRESOLVED
        category = _category(incident.impact, update.status, incident.scheduled)
        status = _status(update.status, incident.scheduled)
        if source.network_id and len(component_names) > 1:
            confidence = CryptoAssociationConfidence.MULTI_ASSET
        published = update.created_at
        provenance = EvidenceProvenance(observed_at=observed, decision_cutoff=observed, evidence_timestamps=(("published_at", published),))
        if confidence is CryptoAssociationConfidence.PROVIDER_MAPPED:
            self._metrics["statuspage_exact_associations"] += 1
        elif confidence is CryptoAssociationConfidence.MULTI_ASSET:
            self._metrics["statuspage_multi_asset_associations"] += 1
        elif confidence is CryptoAssociationConfidence.AMBIGUOUS:
            self._metrics["statuspage_ambiguous_associations"] += 1
        else:
            self._metrics["statuspage_unresolved_associations"] += 1
        return CryptoCatalystEvidence(
            event_type=category, status=status, provider_id=FEDERAL_STATUS_PROVIDER_ID,
            source_name=f"{self.source_name}: {source.canonical_name}",
            source_reference=f"{source.source_id}:{incident.incident_id}:{update.update_id}",
            published_at=published, observed_at=observed, effective_at=incident.started_at,
            project=project, association_confidence=confidence, expected_direction=CryptoCatalystDirection.UNKNOWN,
            title=incident.name, summary=(update.body[:500] if update.body else None), source_url=incident.shortlink,
            provider_event_id=update.update_id, underlying_event_key=f"{source.source_id}:{incident.incident_id}",
            revision=max(1, len(incident.updates)), provenance=provenance, decision_cutoff=observed,
        )


FEDERAL_STATUS_PROVIDER_ID = STATUSPAGE_PROVIDER_ID


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _optional_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")), "status timestamp")


def _components_from_payload(source_id: str, payload: object, maximum: int) -> tuple[StatusComponentRecord, ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("components"), list):
        raise StatusPageProviderError(StatusPageFailureKind.SCHEMA_ERROR, "status page components are missing")
    result = []
    for item in payload["components"][:maximum]:
        if not isinstance(item, Mapping) or not item.get("id") or not item.get("name"):
            raise StatusPageProviderError(StatusPageFailureKind.SCHEMA_ERROR, "status component is malformed")
        result.append(StatusComponentRecord(source_id, str(item["id"]), str(item["name"]), str(item.get("status") or "unknown"), str(item["group_id"]) if item.get("group_id") else None, _optional_timestamp(item.get("created_at")), _optional_timestamp(item.get("updated_at"))))
    return tuple(result)


def _incidents_from_payload(source_id: str, payload: object, maximum: int, maximum_updates: int) -> tuple[StatusIncidentRecord, ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("incidents"), list):
        raise StatusPageProviderError(StatusPageFailureKind.SCHEMA_ERROR, "status page incidents are missing")
    result = []
    for item in payload["incidents"][:maximum]:
        if not isinstance(item, Mapping) or not item.get("id") or not item.get("name") or not item.get("created_at"):
            raise StatusPageProviderError(StatusPageFailureKind.SCHEMA_ERROR, "status incident is malformed")
        updates = []
        for update in (item.get("incident_updates") or [])[:maximum_updates]:
            if not isinstance(update, Mapping) or not update.get("id") or not update.get("created_at"):
                raise StatusPageProviderError(StatusPageFailureKind.SCHEMA_ERROR, "status incident update is malformed")
            affected = tuple(str(value.get("code") or value.get("id")) for value in (update.get("affected_components") or ()) if isinstance(value, Mapping) and (value.get("code") or value.get("id")))
            updates.append(StatusIncidentUpdateRecord(source_id, str(item["id"]), str(update["id"]), str(update.get("status") or item.get("status") or "unknown"), str(update.get("body")) if update.get("body") else None, _optional_timestamp(update["created_at"]), _optional_timestamp(update.get("updated_at")), affected))
        affected_incident = tuple(str(value.get("id") or value.get("code")) for value in (item.get("components") or ()) if isinstance(value, Mapping) and (value.get("id") or value.get("code")))
        result.append(StatusIncidentRecord(source_id, str(item["id"]), str(item["name"]), str(item.get("status") or "unknown"), str(item.get("impact") or "none"), _optional_timestamp(item["created_at"]), _optional_timestamp(item.get("updated_at")), _optional_timestamp(item.get("started_at")), _optional_timestamp(item.get("monitoring_at")), _optional_timestamp(item.get("resolved_at")), str(item.get("shortlink")) if item.get("shortlink") else None, affected_incident, tuple(updates), False))
    return tuple(result)


def _category(impact: str, update_status: str, scheduled: bool) -> CryptoCatalystType:
    if scheduled:
        return CryptoCatalystType.OTHER
    if update_status.casefold() == "resolved":
        return CryptoCatalystType.NETWORK_RECOVERY
    return CryptoCatalystType.NETWORK_OUTAGE if impact.casefold() in {"major", "critical"} else CryptoCatalystType.NETWORK_CONGESTION


def _status(value: str, scheduled: bool) -> CryptoCatalystStatus:
    key = value.casefold()
    if scheduled:
        return CryptoCatalystStatus.SCHEDULED
    if key in {"investigating", "identified", "monitoring", "in_progress"}:
        return CryptoCatalystStatus.ACTIVE
    if key in {"resolved", "postmortem"}:
        return CryptoCatalystStatus.RESOLVED
    return CryptoCatalystStatus.UNKNOWN


__all__ = [
    "STATUSPAGE_PROVIDER_ID", "STATUSPAGE_NORMALIZATION_VERSION", "StatusPageFailureKind",
    "StatusPageProviderError", "OfficialStatusSource", "StatusPagePolicy", "StatusComponentRecord",
    "StatusIncidentUpdateRecord", "StatusIncidentRecord", "StatusPageProviderMetrics", "StatusPageProvider",
]
