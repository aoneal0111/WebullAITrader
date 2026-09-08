"""Bounded, public Bybit announcement evidence adapter.

This module intentionally owns no runtime lifecycle and has no trading/authentication
surface.  It converts the documented public announcement envelope into Phase E
research evidence; identity association is accepted only from an explicit E3 registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from time import sleep
from typing import Any, Mapping, Protocol

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
    CryptoProviderAssetReference,
    CryptoProjectRegistry,
)


BYBIT_PROVIDER_ID = "BYBIT"
BYBIT_ANNOUNCEMENTS_URL = "https://api.bybit.com/v5/announcements/index"
NORMALIZATION_VERSION = "bybit-announcement-v1"
MAX_PAGE_LIMIT = 50
MAX_PAYLOAD_BYTES = 2_000_000


class BybitFailureKind(StrEnum):
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_4XX = "PROVIDER_4XX"
    PROVIDER_5XX = "PROVIDER_5XX"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"


class BybitProviderError(RuntimeError):
    def __init__(self, kind: BybitFailureKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class BybitAnnouncementPolicy:
    enabled: bool = False
    locale: str = "en-US"
    page_limit: int = 20
    maximum_pages: int = 2
    maximum_announcements: int = 100
    maximum_retries: int = 2
    timeout_seconds: float = 5.0
    backoff_seconds: float = 0.25
    poll_seconds: int = 900
    maximum_payload_bytes: int = MAX_PAYLOAD_BYTES

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise ValueError("locale is required")
        if not 1 <= self.page_limit <= MAX_PAGE_LIMIT:
            raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_LIMIT}")
        if min(self.maximum_pages, self.maximum_announcements, self.maximum_payload_bytes) <= 0 or self.maximum_retries < 0:
            raise ValueError("Bybit bounds must be positive")
        if self.timeout_seconds <= 0 or self.backoff_seconds < 0 or self.poll_seconds < 60:
            raise ValueError("invalid Bybit timing policy")


@dataclass(frozen=True, slots=True)
class BybitAnnouncementRaw:
    provider_event_id: str
    title: str
    description: str | None
    type_key: str
    type_title: str | None
    tags: tuple[str, ...]
    source_url: str
    published_at: datetime
    author_timestamp: datetime | None = None
    effective_at: datetime | None = None
    activity_end_at: datetime | None = None
    updated_at: datetime | None = None
    locale: str = "en-US"

    def __post_init__(self) -> None:
        if not self.provider_event_id.strip() or not self.title.strip() or not self.source_url.strip():
            raise ValueError("Bybit event identity, title, and URL are required")
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        object.__setattr__(self, "author_timestamp", _optional_utc(self.author_timestamp, "author_timestamp"))
        object.__setattr__(self, "effective_at", _optional_utc(self.effective_at, "effective_at"))
        object.__setattr__(self, "activity_end_at", _optional_utc(self.activity_end_at, "activity_end_at"))
        object.__setattr__(self, "updated_at", _optional_utc(self.updated_at, "updated_at"))
        if self.effective_at and self.activity_end_at and self.activity_end_at < self.effective_at:
            raise ValueError("activity end precedes start")


@dataclass(frozen=True, slots=True)
class BybitProviderMetrics:
    bybit_requests_attempted: int
    bybit_requests_succeeded: int
    bybit_requests_failed: int
    bybit_rate_limited: int
    bybit_pages_fetched: int
    bybit_raw_announcements: int
    bybit_normalized_events: int
    bybit_duplicates_suppressed: int
    bybit_revisions: int
    bybit_exact_associations: int
    bybit_ambiguous_associations: int
    bybit_unresolved_associations: int
    bybit_last_success_age_seconds: float | None


class BybitTransport(Protocol):
    def get(self, url: str, *, params: Mapping[str, object], timeout: float) -> object: ...


class _HttpxBybitTransport:
    def __init__(self, *, timeout_seconds: float, maximum_payload_bytes: int) -> None:
        self._maximum_payload_bytes = maximum_payload_bytes
        self._client = httpx.Client(
            headers={"Accept": "application/json", "User-Agent": "AtlasCryptoResearch/1.0"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )

    def get(self, url: str, *, params: Mapping[str, object], timeout: float) -> object:
        try:
            response = self._client.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise BybitProviderError(BybitFailureKind.TIMEOUT, "Bybit request timed out") from exc
        except httpx.NetworkError as exc:
            raise BybitProviderError(BybitFailureKind.NETWORK_ERROR, "Bybit network request failed") from exc
        status = getattr(response, "status_code", None)
        if status == 429:
            raise BybitProviderError(BybitFailureKind.RATE_LIMITED, "Bybit rate limit response", status_code=429)
        if isinstance(status, int) and 400 <= status < 500:
            raise BybitProviderError(BybitFailureKind.PROVIDER_4XX, f"Bybit HTTP {status}", status_code=status)
        if isinstance(status, int) and status >= 500:
            raise BybitProviderError(BybitFailureKind.PROVIDER_5XX, f"Bybit HTTP {status}", status_code=status)
        content = getattr(response, "content", b"")
        if isinstance(content, bytes) and len(content) > self._maximum_payload_bytes:
            raise BybitProviderError(BybitFailureKind.MALFORMED_RESPONSE, "Bybit response exceeds payload bound")
        try:
            return response.json()
        except Exception as exc:
            raise BybitProviderError(BybitFailureKind.MALFORMED_RESPONSE, "Bybit response is not JSON") from exc


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None, name: str) -> datetime | None:
    return _utc(value, name) if value is not None else None


def _timestamp(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be an epoch timestamp")
    number = float(value)
    if number > 100_000_000_000:
        number /= 1000.0
    return datetime.fromtimestamp(number, tz=UTC)


def _text(value: object, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("required text is missing")
        return None
    result = str(value).strip()
    if required and not result:
        raise ValueError("required text is empty")
    return result or None


def _category(type_key: str) -> CryptoCatalystType:
    return {
        "new_crypto": CryptoCatalystType.EXCHANGE_LISTING,
        "delistings": CryptoCatalystType.EXCHANGE_DELISTING,
    }.get(type_key, CryptoCatalystType.UNKNOWN)


def _stable_event_id(item: Mapping[str, object], *, type_key: str, published_at: datetime) -> str:
    url = _text(item.get("url"))
    if url:
        return url
    material = "|".join((type_key, _text(item.get("title")) or "", published_at.isoformat()))
    return "bybit:" + sha256(material.encode("utf-8")).hexdigest()


def _raw_from_item(item: Mapping[str, object], *, locale: str) -> BybitAnnouncementRaw:
    type_value = item.get("type")
    if not isinstance(type_value, Mapping):
        raise ValueError("announcement type object is missing")
    type_key = _text(type_value.get("key"), required=True)
    published = _timestamp(item.get("publishTime"), "publishTime") or _timestamp(item.get("dateTimestamp"), "dateTimestamp")
    if published is None:
        raise ValueError("announcement publication timestamp is missing")
    effective = _timestamp(item.get("startDateTimestamp"), "startDateTimestamp")
    if effective is None:
        effective = _timestamp(item.get("startDataTimestamp"), "startDataTimestamp")
    end = _timestamp(item.get("endDateTimestamp"), "endDateTimestamp")
    if end is None:
        end = _timestamp(item.get("endDataTimestamp"), "endDataTimestamp")
    updated = _timestamp(item.get("updatedAt"), "updatedAt")
    title = _text(item.get("title"), required=True)
    url = _text(item.get("url"), required=True)
    tags_value = item.get("tags", ())
    if not isinstance(tags_value, (list, tuple)) or any(not isinstance(tag, str) for tag in tags_value):
        raise ValueError("announcement tags are malformed")
    return BybitAnnouncementRaw(
        provider_event_id=_stable_event_id(item, type_key=type_key, published_at=published),
        title=title,
        description=_text(item.get("description")),
        type_key=type_key,
        type_title=_text(type_value.get("title")),
        tags=tuple(sorted({tag.strip() for tag in tags_value if tag.strip()})),
        source_url=url,
        published_at=published,
        author_timestamp=_timestamp(item.get("dateTimestamp"), "dateTimestamp"),
        effective_at=effective,
        activity_end_at=end,
        updated_at=updated,
        locale=locale,
    )


class BybitAnnouncementsProvider:
    provider_id = BYBIT_PROVIDER_ID
    name = "Bybit Official Announcements"

    def __init__(
        self,
        policy: BybitAnnouncementPolicy = BybitAnnouncementPolicy(),
        *,
        transport: BybitTransport | None = None,
        identity_registry: CryptoAssetIdentityRegistry | None = None,
        project_registry: CryptoProjectRegistry | None = None,
    ) -> None:
        self.policy = policy
        self._transport = transport or _HttpxBybitTransport(
            timeout_seconds=policy.timeout_seconds,
            maximum_payload_bytes=policy.maximum_payload_bytes,
        )
        self._identity_registry = identity_registry
        self._project_registry = project_registry or CryptoProjectRegistry()
        self._metrics = {name: 0 for name in BybitProviderMetrics.__dataclass_fields__ if name != "bybit_last_success_age_seconds"}
        self._last_success: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.policy.enabled

    @property
    def metrics(self) -> BybitProviderMetrics:
        age = None if self._last_success is None else max(0.0, (datetime.now(UTC) - self._last_success).total_seconds())
        return BybitProviderMetrics(**self._metrics, bybit_last_success_age_seconds=age)

    def fetch_page(self, page: int = 1, *, observed_at: datetime | None = None) -> tuple[BybitAnnouncementRaw, ...]:
        if not self.enabled:
            return ()
        if page <= 0 or page > self.policy.maximum_pages:
            raise ValueError("page exceeds configured bound")
        self._metrics["bybit_requests_attempted"] += 1
        payload: object | None = None
        for attempt in range(self.policy.maximum_retries + 1):
            try:
                payload = self._transport.get(
                    BYBIT_ANNOUNCEMENTS_URL,
                    params={"locale": self.policy.locale, "page": page, "limit": self.policy.page_limit},
                    timeout=self.policy.timeout_seconds,
                )
                break
            except BybitProviderError as exc:
                if exc.kind in {BybitFailureKind.RATE_LIMITED, BybitFailureKind.PROVIDER_5XX, BybitFailureKind.NETWORK_ERROR, BybitFailureKind.TIMEOUT} and attempt < self.policy.maximum_retries:
                    if self.policy.backoff_seconds:
                        sleep(self.policy.backoff_seconds * (attempt + 1))
                    continue
                self._metrics["bybit_requests_failed"] += 1
                if exc.kind is BybitFailureKind.RATE_LIMITED:
                    self._metrics["bybit_rate_limited"] += 1
                raise
        self._metrics["bybit_requests_succeeded"] += 1
        self._metrics["bybit_pages_fetched"] += 1
        self._last_success = _utc(observed_at or datetime.now(UTC), "observed_at")
        try:
            if not isinstance(payload, Mapping) or payload.get("retCode") != 0:
                raise BybitProviderError(BybitFailureKind.SCHEMA_ERROR, "Bybit envelope is invalid")
            result = payload.get("result")
            if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
                raise BybitProviderError(BybitFailureKind.SCHEMA_ERROR, "Bybit result.list is missing")
            records: list[BybitAnnouncementRaw] = []
            for item in result["list"][: self.policy.maximum_announcements]:
                if not isinstance(item, Mapping):
                    continue
                try:
                    records.append(_raw_from_item(item, locale=self.policy.locale))
                except (ValueError, TypeError):
                    self._metrics["bybit_requests_failed"] += 1
                    continue
            self._metrics["bybit_raw_announcements"] += len(records)
            return tuple(records)
        except BybitProviderError:
            self._metrics["bybit_requests_failed"] += 1
            raise

    def fetch_since(
        self, since: datetime, *, observed_at: datetime | None = None,
    ) -> tuple[CryptoCatalystEvidence, ...]:
        if not self.enabled:
            return ()
        boundary = _utc(since, "since")
        observed = _utc(observed_at or datetime.now(UTC), "observed_at")
        if observed < boundary:
            raise ValueError("observed_at cannot precede since")
        result: list[CryptoCatalystEvidence] = []
        seen: set[str] = set()
        for page in range(1, self.policy.maximum_pages + 1):
            rows = self.fetch_page(page, observed_at=observed)
            if not rows:
                break
            for raw in rows:
                if raw.provider_event_id in seen:
                    self._metrics["bybit_duplicates_suppressed"] += 1
                    continue
                seen.add(raw.provider_event_id)
                if raw.published_at < boundary or raw.published_at > observed:
                    continue
                try:
                    evidence = self._to_evidence(raw, observed_at=observed, decision_cutoff=observed)
                except (ValueError, TypeError):
                    self._metrics["bybit_requests_failed"] += 1
                    continue
                result.append(evidence)
                self._metrics["bybit_normalized_events"] += 1
        return tuple(result[: self.policy.maximum_announcements])

    def collect(self, as_of: datetime) -> tuple[CryptoCatalystEvidence, ...]:
        return self.fetch_since(as_of, observed_at=as_of)

    def _to_evidence(self, raw: BybitAnnouncementRaw, *, observed_at: datetime, decision_cutoff: datetime | None) -> CryptoCatalystEvidence:
        category = _category(raw.type_key)
        project: CryptoProjectIdentity | None = None
        token_symbol: str | None = None
        associations: tuple[CryptoPairAssociation, ...] = ()
        confidence = CryptoAssociationConfidence.UNRESOLVED
        if self._identity_registry is not None:
            mapped = []
            for tag in raw.tags:
                lookup = self._identity_registry.provider_lookup(BYBIT_PROVIDER_ID, tag, symbol=None)
                if lookup.asset_ids:
                    mapped.extend(lookup.asset_ids)
            if len(set(mapped)) == 1:
                asset_id = mapped[0]
                asset = next((candidate for candidate in self._identity_registry.assets if candidate.asset_id == asset_id), None)
                if asset is not None:
                    token_symbol = asset.symbol
                    if asset.project_id:
                        project = self._identity_registry.project_lookup(asset.project_id)
                    pairs = tuple(pair for pair in self._identity_registry.pairs if pair.base_asset_id == asset_id)
                    associations = tuple(CryptoPairAssociation(pair, CryptoAssociationType.TOKEN, CryptoAssociationConfidence.PROVIDER_MAPPED, "explicit Bybit provider asset mapping") for pair in pairs)
                    confidence = CryptoAssociationConfidence.PROVIDER_MAPPED
            elif len(set(mapped)) > 1:
                confidence = CryptoAssociationConfidence.AMBIGUOUS
        revision = 1 if raw.updated_at is None else 1 + int(raw.updated_at.timestamp())
        if raw.updated_at is not None:
            self._metrics["bybit_revisions"] += 1
        status = CryptoCatalystStatus.SCHEDULED if raw.effective_at and raw.effective_at > observed_at else CryptoCatalystStatus.ANNOUNCED
        return CryptoCatalystEvidence(
            event_type=category, status=status, provider_id=BYBIT_PROVIDER_ID,
            source_name=self.name, source_reference=raw.provider_event_id,
            published_at=raw.published_at, observed_at=observed_at,
            effective_at=raw.effective_at, project=project, token_symbol=token_symbol,
            associated_pairs=associations, association_confidence=confidence,
            expected_direction=CryptoCatalystDirection.UNKNOWN, title=raw.title,
            summary=raw.description[:1000] if raw.description else None,
            source_url=raw.source_url, provider_event_id=raw.provider_event_id,
            underlying_event_key=raw.provider_event_id, revision=revision,
            decision_cutoff=decision_cutoff,
        )


__all__ = [
    "BYBIT_PROVIDER_ID", "BYBIT_ANNOUNCEMENTS_URL", "BybitAnnouncementPolicy",
    "BybitAnnouncementRaw", "BybitAnnouncementsProvider", "BybitFailureKind",
    "BybitProviderError", "BybitProviderMetrics", "NORMALIZATION_VERSION",
]
