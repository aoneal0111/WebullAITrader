"""Bounded, read-only Federal Register evidence for explicitly mapped crypto events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import time
from threading import Lock
from typing import Mapping, Protocol
from urllib.parse import urlencode

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


FEDERAL_REGISTER_PROVIDER_ID = "FEDERAL_REGISTER"
FEDERAL_REGISTER_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
FEDERAL_REGISTER_NORMALIZATION_VERSION = "federal-register-crypto-v1"
MAX_PAGE_SIZE = 1000
MAX_PAGES = 2
MAX_DOCUMENTS = 100
MAX_PAYLOAD_BYTES = 8_000_000


class FederalRegisterFailureKind(StrEnum):
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_4XX = "PROVIDER_4XX"
    PROVIDER_5XX = "PROVIDER_5XX"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class FederalRegisterProviderError(RuntimeError):
    def __init__(self, kind: FederalRegisterFailureKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FederalRegisterPolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    maximum_pages: int = 2
    maximum_documents: int = 100
    per_page: int = 50
    maximum_retries: int = 2
    backoff_seconds: float = 0.5
    poll_seconds: int = 1800
    maximum_payload_bytes: int = MAX_PAYLOAD_BYTES
    conditions: tuple[tuple[str, str], ...] = (("order", "newest"),)

    def __post_init__(self) -> None:
        if not 1 <= self.per_page <= MAX_PAGE_SIZE or not 1 <= self.maximum_pages <= MAX_PAGES:
            raise ValueError("Federal Register page bounds are invalid")
        if not 1 <= self.maximum_documents <= MAX_DOCUMENTS or self.maximum_retries < 0:
            raise ValueError("Federal Register document bounds are invalid")
        if self.timeout_seconds <= 0 or self.backoff_seconds < 0 or self.poll_seconds < 60:
            raise ValueError("Federal Register timing policy is invalid")
        if self.maximum_payload_bytes <= 0:
            raise ValueError("Federal Register payload bound is invalid")


@dataclass(frozen=True, slots=True)
class FederalRegisterDocumentRecord:
    document_number: str
    document_type: str
    title: str
    abstract: str | None
    agency_ids: tuple[int, ...]
    agency_names: tuple[str, ...]
    publication_date: date
    effective_on: date | None
    signing_date: date | None
    comments_close_on: date | None
    docket_ids: tuple[str, ...]
    regulation_id_numbers: tuple[str, ...]
    html_url: str
    pdf_url: str | None
    correction_of: str | None
    publication_precision: str = "DATE"

    def __post_init__(self) -> None:
        if not self.document_number.strip() or not self.document_type.strip() or not self.title.strip():
            raise ValueError("Federal Register document identity is required")
        if not self.html_url.strip():
            raise ValueError("Federal Register source URL is required")
        object.__setattr__(self, "document_number", self.document_number.strip())
        object.__setattr__(self, "document_type", self.document_type.strip())
        object.__setattr__(self, "title", self.title.strip())


@dataclass(frozen=True, slots=True)
class FederalRegisterProviderMetrics:
    federal_register_requests_attempted: int
    federal_register_requests_succeeded: int
    federal_register_requests_failed: int
    federal_register_documents_seen: int
    federal_register_documents_normalized: int
    federal_register_duplicates_suppressed: int
    federal_register_revisions: int
    federal_register_exact_associations: int
    federal_register_multi_asset_associations: int
    federal_register_ambiguous_associations: int
    federal_register_unresolved_associations: int
    federal_register_last_success_age_seconds: float | None


class FederalRegisterTransport(Protocol):
    def get(self, url: str, *, params: Mapping[str, object], timeout: float) -> object: ...


class _HttpxFederalRegisterTransport:
    def __init__(self, policy: FederalRegisterPolicy) -> None:
        self._maximum_payload_bytes = policy.maximum_payload_bytes
        self._client = httpx.Client(
            headers={"Accept": "application/json", "User-Agent": "AtlasCryptoResearch/1.0"},
            timeout=httpx.Timeout(policy.timeout_seconds), follow_redirects=True,
        )

    def get(self, url: str, *, params: Mapping[str, object], timeout: float) -> object:
        try:
            response = self._client.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.TIMEOUT, "Federal Register request timed out") from exc
        except httpx.NetworkError as exc:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.NETWORK_ERROR, "Federal Register network request failed") from exc
        status = getattr(response, "status_code", None)
        if status == 429:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.RATE_LIMITED, "Federal Register rate limit response", status_code=429)
        if isinstance(status, int) and 400 <= status < 500:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.PROVIDER_4XX, f"Federal Register HTTP {status}", status_code=status)
        if isinstance(status, int) and status >= 500:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.PROVIDER_5XX, f"Federal Register HTTP {status}", status_code=status)
        content = getattr(response, "content", b"")
        if isinstance(content, bytes) and len(content) > self._maximum_payload_bytes:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.MALFORMED_RESPONSE, "Federal Register payload exceeds bound")
        try:
            return response.json()
        except Exception as exc:
            raise FederalRegisterProviderError(FederalRegisterFailureKind.MALFORMED_RESPONSE, "Federal Register response is not JSON") from exc


class _RateLimiter:
    def __init__(self, requests_per_second: float = 1.0) -> None:
        self._interval = 1.0 / requests_per_second
        self._last: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last is not None and (remaining := self._interval - (now - self._last)) > 0:
                time.sleep(remaining)
            self._last = time.monotonic()


class FederalRegisterProvider:
    provider_id = FEDERAL_REGISTER_PROVIDER_ID
    source_name = "Federal Register"

    def __init__(self, policy: FederalRegisterPolicy = FederalRegisterPolicy(), *, transport: FederalRegisterTransport | None = None, identity_registry: CryptoAssetIdentityRegistry | None = None, document_asset_ids: Mapping[str, tuple[str, ...]] | None = None, multi_asset_documents: frozenset[str] = frozenset(), taxonomy_overrides: Mapping[str, CryptoCatalystType] | None = None) -> None:
        self.policy = policy
        self._transport = transport or _HttpxFederalRegisterTransport(policy)
        self._identity_registry = identity_registry
        self._document_asset_ids = {str(k): tuple(str(v) for v in values) for k, values in (document_asset_ids or {}).items()}
        self._multi_asset_documents = frozenset(str(item) for item in multi_asset_documents)
        self._taxonomy_overrides = dict(taxonomy_overrides or {})
        self._limiter = _RateLimiter()
        self._metrics = {name: 0 for name in FederalRegisterProviderMetrics.__dataclass_fields__ if name != "federal_register_last_success_age_seconds"}
        self._last_success: datetime | None = None

    @property
    def metrics(self) -> FederalRegisterProviderMetrics:
        age = None if self._last_success is None else max(0.0, (datetime.now(UTC) - self._last_success).total_seconds())
        return FederalRegisterProviderMetrics(**self._metrics, federal_register_last_success_age_seconds=age)

    def fetch_since(self, since: datetime, *, observed_at: datetime | None = None) -> tuple[CryptoCatalystEvidence, ...]:
        if not self.policy.enabled:
            return ()
        boundary = _utc(since, "since")
        observed = _utc(observed_at or datetime.now(UTC), "observed_at")
        if observed < boundary:
            raise ValueError("observed_at cannot precede since")
        output: list[CryptoCatalystEvidence] = []
        seen: set[str] = set()
        for page in range(1, self.policy.maximum_pages + 1):
            try:
                payload = self._get_page(page)
                documents = _records_from_payload(payload, maximum=self.policy.maximum_documents - len(output))
            except FederalRegisterProviderError:
                self._metrics["federal_register_requests_failed"] += 1
                break
            self._metrics["federal_register_documents_seen"] += len(documents)
            if not documents:
                break
            for record in documents:
                if record.document_number in seen:
                    self._metrics["federal_register_duplicates_suppressed"] += 1
                    continue
                seen.add(record.document_number)
                published = _date_datetime(record.publication_date)
                if published < boundary or published > observed:
                    continue
                try:
                    item = self._to_evidence(record, observed_at=observed, decision_cutoff=observed)
                except (ValueError, TypeError):
                    self._metrics["federal_register_requests_failed"] += 1
                    continue
                output.append(item)
                self._metrics["federal_register_documents_normalized"] += 1
                if record.correction_of:
                    self._metrics["federal_register_revisions"] += 1
                if len(output) >= self.policy.maximum_documents:
                    return tuple(output)
            if len(documents) < self.policy.per_page:
                break
        return tuple(output)

    def _get_page(self, page: int) -> object:
        params: dict[str, object] = {key: value for key, value in self.policy.conditions}
        params.update({"page": page, "per_page": self.policy.per_page})
        self._metrics["federal_register_requests_attempted"] += 1
        payload: object | None = None
        for attempt in range(self.policy.maximum_retries + 1):
            self._limiter.wait()
            try:
                payload = self._transport.get(FEDERAL_REGISTER_DOCUMENTS_URL, params=params, timeout=self.policy.timeout_seconds)
                break
            except FederalRegisterProviderError as exc:
                if exc.kind in {FederalRegisterFailureKind.TIMEOUT, FederalRegisterFailureKind.NETWORK_ERROR, FederalRegisterFailureKind.RATE_LIMITED, FederalRegisterFailureKind.PROVIDER_5XX} and attempt < self.policy.maximum_retries:
                    if self.policy.backoff_seconds:
                        time.sleep(self.policy.backoff_seconds * (attempt + 1))
                    continue
                raise
        self._metrics["federal_register_requests_succeeded"] += 1
        self._last_success = datetime.now(UTC)
        if not isinstance(payload, Mapping):
            raise FederalRegisterProviderError(FederalRegisterFailureKind.SCHEMA_ERROR, "Federal Register envelope is not an object")
        return payload

    def _to_evidence(self, record: FederalRegisterDocumentRecord, *, observed_at: datetime, decision_cutoff: datetime) -> CryptoCatalystEvidence:
        asset_ids = self._document_asset_ids.get(record.document_number, ())
        pairs: list[CryptoPairAssociation] = []
        projects: list[CryptoProjectIdentity] = []
        for asset_id in asset_ids:
            if self._identity_registry is None:
                continue
            asset = next((item for item in self._identity_registry.assets if item.asset_id == asset_id), None)
            if asset is None:
                continue
            if asset.project_id:
                project = self._identity_registry.project_lookup(asset.project_id)
                if project is not None:
                    projects.append(project)
            for pair in self._identity_registry.pairs:
                if pair.base_asset_id == asset_id:
                    pairs.append(CryptoPairAssociation(pair, CryptoAssociationType.TOKEN, CryptoAssociationConfidence.EXACT, "explicit Federal Register mapping"))
        if record.document_number in self._multi_asset_documents or len(asset_ids) > 1:
            confidence = CryptoAssociationConfidence.MULTI_ASSET
        elif pairs:
            confidence = CryptoAssociationConfidence.EXACT
        elif asset_ids:
            confidence = CryptoAssociationConfidence.AMBIGUOUS if self._identity_registry is None else CryptoAssociationConfidence.UNRESOLVED
        else:
            confidence = CryptoAssociationConfidence.UNRESOLVED
        category = self._taxonomy_overrides.get(record.document_number, _category(record.document_type))
        published = _date_datetime(record.publication_date)
        effective = _date_datetime(record.effective_on) if record.effective_on else None
        status = _status(record, observed_at)
        if record.correction_of:
            status = CryptoCatalystStatus.REVISED
        timestamps = [("published_at", published)]
        if effective is not None and effective <= decision_cutoff:
            timestamps.append(("effective_at", effective))
        provenance = EvidenceProvenance(observed_at=observed_at, decision_cutoff=decision_cutoff, evidence_timestamps=tuple(timestamps))
        self._metrics["federal_register_revisions"] += 0
        if confidence is CryptoAssociationConfidence.EXACT:
            self._metrics["federal_register_exact_associations"] += 1
        elif confidence is CryptoAssociationConfidence.MULTI_ASSET:
            self._metrics["federal_register_multi_asset_associations"] += 1
        elif confidence is CryptoAssociationConfidence.AMBIGUOUS:
            self._metrics["federal_register_ambiguous_associations"] += 1
        else:
            self._metrics["federal_register_unresolved_associations"] += 1
        return CryptoCatalystEvidence(
            event_type=category, status=status, provider_id=FEDERAL_REGISTER_PROVIDER_ID,
            source_name=self.source_name, source_reference=record.document_number,
            published_at=published, observed_at=observed_at, effective_at=effective,
            project=projects[0] if len(projects) == 1 else None,
            associated_pairs=tuple(pairs), association_confidence=confidence,
            expected_direction=CryptoCatalystDirection.UNKNOWN, title=record.title,
            summary=record.abstract, source_url=record.html_url,
            provider_event_id=record.document_number, underlying_event_key=record.document_number,
            revision=2 if record.correction_of else 1, supersedes=record.correction_of,
            provenance=provenance, decision_cutoff=decision_cutoff,
        )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _date_datetime(value: date | None) -> datetime | None:
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC) if value else None


def _date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


def _tuple_text(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("Federal Register list field is malformed")
    return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))


def _records_from_payload(payload: Mapping[str, object], *, maximum: int) -> tuple[FederalRegisterDocumentRecord, ...]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise FederalRegisterProviderError(FederalRegisterFailureKind.SCHEMA_ERROR, "Federal Register results are missing")
    records: list[FederalRegisterDocumentRecord] = []
    for item in results[: max(0, maximum)]:
        if not isinstance(item, Mapping):
            raise FederalRegisterProviderError(FederalRegisterFailureKind.SCHEMA_ERROR, "Federal Register result is malformed")
        agencies = item.get("agencies", [])
        if not isinstance(agencies, list):
            raise FederalRegisterProviderError(FederalRegisterFailureKind.SCHEMA_ERROR, "Federal Register agencies are malformed")
        ids = tuple(int(a["id"]) for a in agencies if isinstance(a, Mapping) and isinstance(a.get("id"), int))
        names = tuple(str(a.get("name") or a.get("raw_name")).strip() for a in agencies if isinstance(a, Mapping) and (a.get("name") or a.get("raw_name")))
        correction = item.get("correction_of")
        if isinstance(correction, Mapping):
            correction = correction.get("document_number")
        records.append(FederalRegisterDocumentRecord(
            document_number=str(item.get("document_number") or ""), document_type=str(item.get("type") or ""),
            title=str(item.get("title") or ""), abstract=str(item.get("abstract")).strip() if item.get("abstract") else None,
            agency_ids=ids, agency_names=names, publication_date=date.fromisoformat(str(item.get("publication_date"))),
            effective_on=_date(item.get("effective_on")), signing_date=_date(item.get("signing_date")),
            comments_close_on=_date(item.get("comments_close_on")), docket_ids=_tuple_text(item.get("docket_ids")),
            regulation_id_numbers=_tuple_text(item.get("regulation_id_numbers")), html_url=str(item.get("html_url") or ""),
            pdf_url=str(item.get("pdf_url")) if item.get("pdf_url") else None, correction_of=str(correction) if correction else None,
        ))
    return tuple(records)


def _category(document_type: str) -> CryptoCatalystType:
    return CryptoCatalystType.REGULATORY_ACTION if document_type in {"Rule", "Proposed Rule", "Notice", "Presidential Document", "Correction"} else CryptoCatalystType.UNKNOWN


def _status(record: FederalRegisterDocumentRecord, observed_at: datetime) -> CryptoCatalystStatus:
    if record.document_type == "Proposed Rule":
        return CryptoCatalystStatus.ANNOUNCED
    if record.effective_on:
        return CryptoCatalystStatus.ACTIVE if _date_datetime(record.effective_on) <= observed_at else CryptoCatalystStatus.SCHEDULED
    return CryptoCatalystStatus.ANNOUNCED


__all__ = [
    "FEDERAL_REGISTER_PROVIDER_ID", "FEDERAL_REGISTER_DOCUMENTS_URL", "FEDERAL_REGISTER_NORMALIZATION_VERSION",
    "FederalRegisterFailureKind", "FederalRegisterProviderError", "FederalRegisterPolicy",
    "FederalRegisterDocumentRecord", "FederalRegisterProviderMetrics", "FederalRegisterProvider",
]
