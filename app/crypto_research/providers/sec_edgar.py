"""Bounded, read-only SEC EDGAR evidence for explicitly registered crypto products."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
import math
import re
import time
from threading import Lock
from typing import Any, Mapping, Protocol
from urllib.parse import quote

import httpx

from app.catalysts.sec_edgar import SECEdgarPolicy
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


SEC_PROVIDER_ID = "SEC_EDGAR"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
SEC_NORMALIZATION_VERSION = "sec-crypto-edgar-v1"
MAX_PRODUCTS = 128
MAX_FILINGS_PER_PRODUCT = 100
MAX_PAYLOAD_BYTES = 8_000_000
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class SECCryptoFailureKind(StrEnum):
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_4XX = "PROVIDER_4XX"
    PROVIDER_5XX = "PROVIDER_5XX"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class SECCryptoProviderError(RuntimeError):
    def __init__(self, kind: SECCryptoFailureKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SECETFProductIdentity:
    product_id: str
    issuer: str
    registrant_cik: int
    product_name: str
    underlying_asset_ids: tuple[str, ...]
    exchange_ticker: str | None = None
    identity_version: str = "sec-etf-product-v1"

    def __post_init__(self) -> None:
        if not self.product_id.strip() or not self.issuer.strip() or not self.product_name.strip():
            raise ValueError("SEC product identity fields are required")
        if isinstance(self.registrant_cik, bool) or self.registrant_cik <= 0:
            raise ValueError("registrant_cik must be positive")
        if not self.underlying_asset_ids:
            raise ValueError("explicit underlying asset identity is required")
        object.__setattr__(self, "underlying_asset_ids", tuple(sorted({str(item).strip() for item in self.underlying_asset_ids if str(item).strip()})))
        if not self.underlying_asset_ids:
            raise ValueError("explicit underlying asset identity is required")
        object.__setattr__(self, "exchange_ticker", self.exchange_ticker.strip().upper() if self.exchange_ticker else None)


@dataclass(frozen=True, slots=True)
class SECProviderPolicy:
    """Crypto adapter policy; it delegates fair-access validation to equity SEC policy."""

    user_agent: str
    enabled: bool = False
    freshness_days: int = 30
    timeout_seconds: float = 10.0
    requests_per_second: float = 2.0
    maximum_products: int = 8
    maximum_filings_per_product: int = 100
    maximum_retries: int = 2
    backoff_seconds: float = 0.5
    maximum_payload_bytes: int = MAX_PAYLOAD_BYTES

    def __post_init__(self) -> None:
        base = SECEdgarPolicy(
            user_agent=self.user_agent,
            freshness_days=self.freshness_days,
            timeout_seconds=self.timeout_seconds,
            requests_per_second=min(self.requests_per_second, 5.0),
            max_submissions_cache_entries=max(1, self.maximum_products),
        )
        object.__setattr__(self, "user_agent", base.user_agent)
        if self.freshness_days < 0 or self.maximum_products <= 0 or self.maximum_filings_per_product <= 0:
            raise ValueError("SEC crypto bounds must be positive")
        if self.maximum_products > MAX_PRODUCTS or self.maximum_filings_per_product > MAX_FILINGS_PER_PRODUCT:
            raise ValueError("SEC crypto bounds exceed configured maximum")
        if self.maximum_retries < 0 or self.timeout_seconds <= 0 or self.requests_per_second <= 0 or self.requests_per_second > 5:
            raise ValueError("invalid SEC crypto request policy")
        if self.backoff_seconds < 0 or self.maximum_payload_bytes <= 0:
            raise ValueError("invalid SEC crypto bounds")


@dataclass(frozen=True, slots=True)
class SECFilingRecord:
    accession_number: str
    registrant_cik: int
    registrant_name: str
    form: str
    filing_date: date
    published_at: datetime
    primary_document: str | None
    report_date: date | None = None
    acceptance_datetime: datetime | None = None
    supersedes_accession: str | None = None

    def __post_init__(self) -> None:
        if not _ACCESSION.fullmatch(self.accession_number):
            raise ValueError("invalid SEC accession number")
        if self.registrant_cik <= 0 or not self.registrant_name.strip() or not self.form.strip():
            raise ValueError("invalid SEC filing identity")
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        if self.acceptance_datetime is not None:
            object.__setattr__(self, "acceptance_datetime", _utc(self.acceptance_datetime, "acceptance_datetime"))
        if self.supersedes_accession and not _ACCESSION.fullmatch(self.supersedes_accession):
            raise ValueError("invalid superseded accession")


@dataclass(frozen=True, slots=True)
class SECCryptoProviderMetrics:
    sec_crypto_requests_attempted: int
    sec_crypto_requests_succeeded: int
    sec_crypto_requests_failed: int
    sec_crypto_filings_seen: int
    sec_crypto_filings_normalized: int
    sec_crypto_duplicates_suppressed: int
    sec_crypto_amendments: int
    sec_crypto_exact_associations: int
    sec_crypto_ambiguous_associations: int
    sec_crypto_unresolved_associations: int
    sec_crypto_last_success_age_seconds: float | None


class SECTransport(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> object: ...


class _HttpxSECTransport:
    def __init__(self, policy: SECProviderPolicy) -> None:
        self._maximum_payload_bytes = policy.maximum_payload_bytes
        self._client = httpx.Client(
            headers={"User-Agent": policy.user_agent, "Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(policy.timeout_seconds), follow_redirects=True,
        )

    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> object:
        try:
            response = self._client.get(url, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise SECCryptoProviderError(SECCryptoFailureKind.TIMEOUT, "SEC request timed out") from exc
        except httpx.NetworkError as exc:
            raise SECCryptoProviderError(SECCryptoFailureKind.NETWORK_ERROR, "SEC network request failed") from exc
        status = getattr(response, "status_code", None)
        if status == 429:
            raise SECCryptoProviderError(SECCryptoFailureKind.RATE_LIMITED, "SEC rate limit response", status_code=429)
        if isinstance(status, int) and 400 <= status < 500:
            raise SECCryptoProviderError(SECCryptoFailureKind.PROVIDER_4XX, f"SEC HTTP {status}", status_code=status)
        if isinstance(status, int) and status >= 500:
            raise SECCryptoProviderError(SECCryptoFailureKind.PROVIDER_5XX, f"SEC HTTP {status}", status_code=status)
        content = getattr(response, "content", b"")
        if isinstance(content, bytes) and len(content) > self._maximum_payload_bytes:
            raise SECCryptoProviderError(SECCryptoFailureKind.MALFORMED_RESPONSE, "SEC response exceeds payload bound")
        try:
            return response.json()
        except Exception as exc:
            raise SECCryptoProviderError(SECCryptoFailureKind.MALFORMED_RESPONSE, "SEC response is not JSON") from exc


class _RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second
        self._last: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last is not None:
                remaining = self._interval - (now - self._last)
                if remaining > 0:
                    time.sleep(remaining)
            self._last = time.monotonic()


class SECCryptoEdgarProvider:
    provider_id = SEC_PROVIDER_ID
    source_name = "SEC EDGAR"

    def __init__(
        self,
        policy: SECProviderPolicy,
        *,
        products: tuple[SECETFProductIdentity, ...] = (),
        transport: SECTransport | None = None,
        identity_registry: CryptoAssetIdentityRegistry | None = None,
    ) -> None:
        if not isinstance(policy, SECProviderPolicy):
            raise TypeError("policy must be SECProviderPolicy")
        if len(products) > policy.maximum_products:
            raise ValueError("SEC product bound exceeded")
        self.policy = policy
        self.products = tuple(products)
        self._transport = transport or _HttpxSECTransport(policy)
        self._identity_registry = identity_registry
        self._limiter = _RateLimiter(policy.requests_per_second)
        self._metrics = {name: 0 for name in SECCryptoProviderMetrics.__dataclass_fields__ if name != "sec_crypto_last_success_age_seconds"}
        self._last_success: datetime | None = None

    @property
    def metrics(self) -> SECCryptoProviderMetrics:
        age = None if self._last_success is None else max(0.0, (datetime.now(UTC) - self._last_success).total_seconds())
        return SECCryptoProviderMetrics(**self._metrics, sec_crypto_last_success_age_seconds=age)

    def fetch_since(self, since: datetime, *, observed_at: datetime | None = None) -> tuple[CryptoCatalystEvidence, ...]:
        if not self.policy.enabled:
            return ()
        boundary = _utc(since, "since")
        observed = _utc(observed_at or datetime.now(UTC), "observed_at")
        if observed < boundary:
            raise ValueError("observed_at cannot precede since")
        output: list[CryptoCatalystEvidence] = []
        seen: set[str] = set()
        for product in self.products[: self.policy.maximum_products]:
            try:
                payload = self._get_submissions(product.registrant_cik)
                records = _records_from_payload(payload, product=product, maximum=self.policy.maximum_filings_per_product)
            except SECCryptoProviderError:
                self._metrics["sec_crypto_requests_failed"] += 1
                continue
            except (ValueError, TypeError):
                self._metrics["sec_crypto_requests_failed"] += 1
                continue
            self._metrics["sec_crypto_filings_seen"] += len(records)
            for record in records:
                if record.accession_number in seen:
                    self._metrics["sec_crypto_duplicates_suppressed"] += 1
                    continue
                seen.add(record.accession_number)
                if record.published_at < boundary or record.published_at > observed:
                    continue
                try:
                    evidence = self._to_evidence(record, product=product, observed_at=observed, decision_cutoff=observed)
                except (ValueError, TypeError):
                    self._metrics["sec_crypto_requests_failed"] += 1
                    continue
                output.append(evidence)
                self._metrics["sec_crypto_filings_normalized"] += 1
        return tuple(output)

    def _get_submissions(self, cik: int) -> Mapping[str, object]:
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        payload: object | None = None
        self._metrics["sec_crypto_requests_attempted"] += 1
        for attempt in range(self.policy.maximum_retries + 1):
            self._limiter.wait()
            try:
                payload = self._transport.get(url, headers={"User-Agent": self.policy.user_agent, "Accept": "application/json"}, timeout=self.policy.timeout_seconds)
                break
            except SECCryptoProviderError as exc:
                if exc.kind in {SECCryptoFailureKind.TIMEOUT, SECCryptoFailureKind.NETWORK_ERROR, SECCryptoFailureKind.RATE_LIMITED, SECCryptoFailureKind.PROVIDER_5XX} and attempt < self.policy.maximum_retries:
                    if self.policy.backoff_seconds:
                        time.sleep(self.policy.backoff_seconds * (attempt + 1))
                    continue
                raise
        self._metrics["sec_crypto_requests_succeeded"] += 1
        self._last_success = datetime.now(UTC)
        if not isinstance(payload, Mapping):
            raise SECCryptoProviderError(SECCryptoFailureKind.SCHEMA_ERROR, "SEC submissions response is not an object")
        return payload

    def _to_evidence(self, record: SECFilingRecord, *, product: SECETFProductIdentity, observed_at: datetime, decision_cutoff: datetime) -> CryptoCatalystEvidence:
        pairs: list[CryptoPairAssociation] = []
        projects: list[CryptoProjectIdentity] = []
        confidence = CryptoAssociationConfidence.UNRESOLVED
        if self._identity_registry is not None:
            for asset_id in product.underlying_asset_ids:
                asset = next((item for item in self._identity_registry.assets if item.asset_id == asset_id), None)
                if asset is None:
                    continue
                if asset.project_id:
                    project = self._identity_registry.project_lookup(asset.project_id)
                    if project is not None:
                        projects.append(project)
                for pair in self._identity_registry.pairs:
                    if pair.base_asset_id == asset_id:
                        pairs.append(CryptoPairAssociation(pair, CryptoAssociationType.TOKEN, CryptoAssociationConfidence.EXACT, "explicit SEC product underlying mapping"))
            if len(pairs) > 0:
                confidence = CryptoAssociationConfidence.MULTI_ASSET if len(product.underlying_asset_ids) > 1 else CryptoAssociationConfidence.EXACT
            elif not projects:
                confidence = CryptoAssociationConfidence.UNRESOLVED
        category = _category_for_form(record.form)
        status = CryptoCatalystStatus.COMPLETED if record.form == "EFFECT" else CryptoCatalystStatus.ANNOUNCED
        published = record.published_at
        provenance = EvidenceProvenance(observed_at=observed_at, decision_cutoff=decision_cutoff, evidence_timestamps=(("published_at", published), ("filing_date", datetime.combine(record.filing_date, datetime.min.time(), tzinfo=UTC))))
        source_url = _filing_url(record)
        self._metrics["sec_crypto_amendments"] += int(record.form.endswith("/A"))
        if confidence is CryptoAssociationConfidence.EXACT:
            self._metrics["sec_crypto_exact_associations"] += 1
        elif confidence is CryptoAssociationConfidence.MULTI_ASSET:
            self._metrics["sec_crypto_exact_associations"] += 1
        else:
            self._metrics["sec_crypto_unresolved_associations"] += 1
        return CryptoCatalystEvidence(
            event_type=category, status=status, provider_id=SEC_PROVIDER_ID,
            source_name=self.source_name, source_reference=record.accession_number,
            published_at=published, observed_at=observed_at,
            project=projects[0] if len(projects) == 1 else None,
            token_symbol=None, associated_pairs=tuple(pairs),
            association_confidence=confidence, expected_direction=CryptoCatalystDirection.UNKNOWN,
            title=f"SEC {record.form} filing - {product.product_name}",
            summary=f"{record.registrant_name} ({record.registrant_cik:010d}) {record.form} accession {record.accession_number}",
            source_url=source_url, provider_event_id=record.accession_number,
            underlying_event_key=record.accession_number, revision=2 if record.form.endswith("/A") else 1,
            supersedes=record.supersedes_accession, provenance=provenance,
            decision_cutoff=decision_cutoff,
        )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: object, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed, name)


def _filing_date(value: object) -> date:
    return date.fromisoformat(str(value))


def _recognized_form(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    form = " ".join(value.strip().upper().split())
    allowed = {
        "S-1", "S-1/A", "S-3", "S-3/A", "N-1A", "N-1A/A", "EFFECT",
        "POS AM", "FWP", "424I", "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A",
    }
    return form if form in allowed or re.fullmatch(r"424B[A-Z0-9-]*(?:/A)?", form) else None


def _records_from_payload(payload: Mapping[str, object], *, product: SECETFProductIdentity, maximum: int) -> tuple[SECFilingRecord, ...]:
    filings = payload.get("filings")
    if not isinstance(filings, Mapping) or not isinstance(filings.get("recent"), Mapping):
        raise SECCryptoProviderError(SECCryptoFailureKind.SCHEMA_ERROR, "SEC submissions recent filings are missing")
    recent = filings["recent"]
    registrant_name = payload.get("name") if isinstance(payload.get("name"), str) else product.issuer
    names = ("form", "filingDate", "accessionNumber", "primaryDocument")
    columns: dict[str, list[object]] = {}
    for name in names:
        value = recent.get(name)
        if not isinstance(value, list):
            raise SECCryptoProviderError(SECCryptoFailureKind.SCHEMA_ERROR, f"SEC submissions {name} column is missing")
        columns[name] = value
    count = min(maximum, len(columns["form"]))
    if any(len(value) < count for value in columns.values()):
        raise SECCryptoProviderError(SECCryptoFailureKind.SCHEMA_ERROR, "SEC submissions columns have different lengths")
    acceptance = recent.get("acceptanceDateTime", [])
    report_dates = recent.get("reportDate", [])
    records: list[SECFilingRecord] = []
    for index in range(count):
        form = _recognized_form(columns["form"][index])
        if form is None:
            continue
        accession = str(columns["accessionNumber"][index]).strip()
        filing_date = _filing_date(columns["filingDate"][index])
        accepted = _timestamp(acceptance[index], "acceptance_datetime") if isinstance(acceptance, list) and index < len(acceptance) else None
        published = accepted or datetime.combine(filing_date, datetime.min.time(), tzinfo=UTC)
        report = _filing_date(report_dates[index]) if isinstance(report_dates, list) and index < len(report_dates) and report_dates[index] else None
        document = str(columns["primaryDocument"][index]).strip() if columns["primaryDocument"][index] else None
        records.append(SECFilingRecord(accession, product.registrant_cik, registrant_name, form, filing_date, published, document, report, accepted))
    return tuple(records)


def _filing_url(record: SECFilingRecord) -> str | None:
    if not record.primary_document or "\\" in record.primary_document or ".." in record.primary_document:
        return None
    document = quote(record.primary_document.lstrip("/"), safe="/-_.")
    return SEC_ARCHIVES_URL.format(cik=record.registrant_cik, accession=record.accession_number.replace("-", ""), document=document)


def _category_for_form(form: str) -> CryptoCatalystType:
    if form == "EFFECT":
        return CryptoCatalystType.ETF_APPROVAL
    if form in {"S-1", "S-1/A", "S-3", "S-3/A", "N-1A", "N-1A/A", "POS AM", "FWP", "424I"} or form.startswith("424B"):
        return CryptoCatalystType.ETF_FILING
    return CryptoCatalystType.REGULATORY_ACTION


__all__ = [
    "SEC_PROVIDER_ID", "SEC_SUBMISSIONS_URL", "SEC_NORMALIZATION_VERSION",
    "SECCryptoFailureKind", "SECCryptoProviderError", "SECETFProductIdentity",
    "SECProviderPolicy", "SECFilingRecord", "SECCryptoProviderMetrics", "SECCryptoEdgarProvider",
]
