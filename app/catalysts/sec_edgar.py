from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
import math
import re
from threading import Lock
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.catalysts.models import CatalystEvidence
from app.momentum_scanner.models import CatalystStatus, CatalystType


_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,19}$")
_EASTERN = ZoneInfo("America/New_York")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SECEdgarPolicy:
    """Fair-access, freshness, and cache policy for SEC public data."""

    user_agent: str
    freshness_days: int = 3
    timeout_seconds: float = 10.0
    ticker_cache_seconds: float = 86_400.0
    submissions_cache_seconds: float = 900.0
    max_ticker_entries: int = 25_000
    max_submissions_cache_entries: int = 2_048
    failure_cooldown_seconds: float = 60.0
    requests_per_second: float = 5.0

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ValueError("SEC EDGAR user agent is required")
        if self.freshness_days < 0:
            raise ValueError("SEC EDGAR freshness_days must not be negative")
        for name in (
            "timeout_seconds",
            "ticker_cache_seconds",
            "submissions_cache_seconds",
            "failure_cooldown_seconds",
            "requests_per_second",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"SEC EDGAR {name} must be positive")
        if self.requests_per_second > 10:
            raise ValueError("SEC EDGAR requests_per_second must not exceed 10")
        for name in (
            "max_ticker_entries",
            "max_submissions_cache_entries",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"SEC EDGAR {name} must be a positive integer")
        object.__setattr__(self, "user_agent", self.user_agent.strip())


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    value: object
    expires_at: float


@dataclass(frozen=True, slots=True)
class _Filing:
    form: str
    accession_number: str
    published_at: datetime
    primary_document: str | None


class _MalformedSECResponse(ValueError):
    pass


class _SECUnavailable(RuntimeError):
    pass


class _RateLimiter:
    def __init__(
        self,
        requests_per_second: float,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._minimum_interval = 1.0 / requests_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_request_at: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self._minimum_interval - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._monotonic()
            self._last_request_at = now


_GLOBAL_RATE_LIMITER = _RateLimiter(
    5.0,
    monotonic=time.monotonic,
    sleep=time.sleep,
)


class SECEdgarCatalystProvider:
    """Read recent filing evidence directly from SEC structured data APIs."""

    name = "SEC_EDGAR"

    def __init__(
        self,
        policy: SECEdgarPolicy,
        *,
        client: object | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(policy, SECEdgarPolicy):
            raise TypeError("policy must be SECEdgarPolicy")
        self._policy = policy
        self._client = client if client is not None else httpx.Client(
            headers={
                "User-Agent": policy.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=httpx.Timeout(policy.timeout_seconds),
            follow_redirects=True,
        )
        self._monotonic = monotonic
        local_rate_limiter = _RateLimiter(
            min(policy.requests_per_second, 5.0),
            monotonic=monotonic,
            sleep=sleep,
        )
        self._rate_limiters = (
            (_GLOBAL_RATE_LIMITER, local_rate_limiter)
            if monotonic is time.monotonic and sleep is time.sleep
            else (local_rate_limiter,)
        )
        self._ticker_cache: _CacheEntry | None = None
        self._submissions_cache: dict[int, _CacheEntry] = {}
        self._cache_lock = Lock()
        self._ticker_fetch_lock = Lock()
        self._submissions_fetch_lock = Lock()
        self._availability_lock = Lock()
        self._unavailable_until = 0.0
        log_sec_edgar_provider_state(enabled=True)

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return self._negative(symbol, CatalystStatus.FALSE)
        now = _utc_as_of(as_of)
        return self._get_evidence_sync(normalized, now)

    def _get_evidence_sync(
        self,
        normalized: str,
        now: datetime,
    ) -> CatalystEvidence:
        try:
            cik = self._cik_for_symbol(normalized)
            if cik is None:
                return self._negative(normalized, CatalystStatus.FALSE)
            payload = self._company_submissions(cik)
            filing = _latest_relevant_filing(
                payload,
                as_of=now,
                freshness_days=self._policy.freshness_days,
            )
        except _SECUnavailable:
            return self._negative(normalized, CatalystStatus.UNAVAILABLE)
        except _MalformedSECResponse:
            return self._negative(normalized, CatalystStatus.UNKNOWN)

        if filing is None:
            return self._negative(normalized, CatalystStatus.FALSE)
        source_url = _filing_url(cik, filing)
        _LOGGER.info(
            "sec_edgar_event event=filing_evidence_found "
            "symbol=%s form=%s accession_number=%s source=%s",
            normalized,
            filing.form,
            filing.accession_number,
            self.name,
        )
        return CatalystEvidence(
            symbol=normalized,
            catalyst_type=CatalystType.SEC_FILING,
            status=CatalystStatus.TRUE,
            headline=f"SEC {filing.form} filing",
            source=self.name,
            published_at=filing.published_at,
            source_url=source_url,
            provider_event_id=filing.accession_number,
            canonical_event_id=f"sec-filing:{filing.accession_number.casefold()}",
        )

    def _negative(
        self, symbol: str, status: CatalystStatus
    ) -> CatalystEvidence:
        normalized = str(symbol).strip().upper() or "UNKNOWN"
        return CatalystEvidence(
            symbol=normalized,
            catalyst_type=CatalystType.NONE,
            status=status,
            source=self.name,
        )

    def _cik_for_symbol(self, symbol: str) -> int | None:
        now = self._monotonic()
        with self._cache_lock:
            cached = self._ticker_cache
            if cached is not None and cached.expires_at > now:
                _log_cache("ticker_mapping", hit=True)
                return _mapping_value(cached.value, symbol)
        _log_cache("ticker_mapping", hit=False)
        with self._ticker_fetch_lock:
            now = self._monotonic()
            with self._cache_lock:
                cached = self._ticker_cache
                if cached is not None and cached.expires_at > now:
                    _log_cache("ticker_mapping", hit=True)
                    return _mapping_value(cached.value, symbol)
            payload = self._request_json(_TICKER_URL)
            mapping = _ticker_mapping(
                payload,
                max_entries=self._policy.max_ticker_entries,
            )
            with self._cache_lock:
                self._ticker_cache = _CacheEntry(
                    mapping, now + self._policy.ticker_cache_seconds
                )
            return mapping.get(symbol)

    def _company_submissions(self, cik: int) -> Mapping[str, object]:
        now = self._monotonic()
        with self._cache_lock:
            cached = self._submissions_cache.get(cik)
            if cached is not None and cached.expires_at > now:
                _log_cache("recent_submissions", hit=True)
                return _mapping(cached.value)
        _log_cache("recent_submissions", hit=False)
        with self._submissions_fetch_lock:
            now = self._monotonic()
            with self._cache_lock:
                cached = self._submissions_cache.get(cik)
                if cached is not None and cached.expires_at > now:
                    _log_cache("recent_submissions", hit=True)
                    return _mapping(cached.value)
            payload = self._request_json(_SUBMISSIONS_URL.format(cik=cik))
            if not isinstance(payload, Mapping):
                raise _MalformedSECResponse(
                    "SEC submissions payload must be an object"
                )
            result = dict(payload)
            with self._cache_lock:
                self._submissions_cache = {
                    key: entry
                    for key, entry in self._submissions_cache.items()
                    if entry.expires_at > now
                }
                if (
                    len(self._submissions_cache)
                    >= self._policy.max_submissions_cache_entries
                ):
                    oldest = min(
                        self._submissions_cache,
                        key=lambda key: self._submissions_cache[key].expires_at,
                    )
                    self._submissions_cache.pop(oldest)
                self._submissions_cache[cik] = _CacheEntry(
                    result, now + self._policy.submissions_cache_seconds
                )
            return result

    def _request_json(self, url: str) -> object:
        endpoint = _endpoint_name(url)
        with self._availability_lock:
            if self._unavailable_until > self._monotonic():
                _LOGGER.debug(
                    "sec_edgar_event event=provider_unavailable "
                    "reason=failure_cooldown endpoint=%s",
                    endpoint,
                )
                raise _SECUnavailable("SEC request is in failure cooldown")
        for rate_limiter in self._rate_limiters:
            rate_limiter.wait()
        try:
            response = self._client.get(
                url,
                headers={"User-Agent": self._policy.user_agent},
                timeout=self._policy.timeout_seconds,
            )
        except Exception as exc:
            _log_request_failure(endpoint, reason="network", error_type=type(exc).__name__)
            self._mark_unavailable(reason="network", endpoint=endpoint)
            raise _SECUnavailable("SEC request failed") from exc
        try:
            status = int(getattr(response, "status_code", 0))
        except (TypeError, ValueError) as exc:
            _log_request_failure(
                endpoint,
                reason="malformed_response",
                error_type=type(exc).__name__,
            )
            self._mark_unavailable(
                reason="malformed_response",
                endpoint=endpoint,
            )
            raise _SECUnavailable("SEC response status is malformed") from exc
        if status == 403 or status == 429 or status >= 500 or status == 0:
            _log_request_failure(endpoint, reason="http", status=status)
            self._mark_unavailable(reason="http", endpoint=endpoint, status=status)
            raise _SECUnavailable(f"SEC request unavailable: HTTP {status}")
        if status < 200 or status >= 300:
            _log_request_failure(endpoint, reason="http", status=status)
            raise _SECUnavailable(f"SEC request failed: HTTP {status}")
        try:
            payload = response.json()
        except Exception as exc:
            _log_request_failure(
                endpoint,
                reason="malformed_json",
                status=status,
                error_type=type(exc).__name__,
            )
            raise _MalformedSECResponse("SEC response was not valid JSON") from exc
        _LOGGER.info(
            "sec_edgar_event event=request_success endpoint=%s status=%s",
            endpoint,
            status,
        )
        return payload

    def _mark_unavailable(
        self,
        *,
        reason: str,
        endpoint: str,
        status: int | None = None,
    ) -> None:
        with self._availability_lock:
            now = self._monotonic()
            was_available = self._unavailable_until <= now
            self._unavailable_until = max(
                self._unavailable_until,
                now + self._policy.failure_cooldown_seconds,
            )
        if was_available:
            _LOGGER.warning(
                "sec_edgar_event event=provider_unavailable "
                "reason=%s endpoint=%s status=%s",
                reason,
                endpoint,
                status if status is not None else "none",
            )


def log_sec_edgar_provider_state(*, enabled: bool) -> None:
    """Log provider construction state without configuration values."""

    _LOGGER.info(
        "sec_edgar_event event=provider_state enabled=%s",
        str(bool(enabled)).lower(),
    )


def _log_cache(cache: str, *, hit: bool) -> None:
    _LOGGER.debug(
        "sec_edgar_event event=cache_%s cache=%s",
        "hit" if hit else "miss",
        cache,
    )


def _log_request_failure(
    endpoint: str,
    *,
    reason: str,
    status: int | None = None,
    error_type: str | None = None,
) -> None:
    _LOGGER.warning(
        "sec_edgar_event event=request_failure endpoint=%s "
        "reason=%s status=%s error_type=%s",
        endpoint,
        reason,
        status if status is not None else "none",
        error_type or "none",
    )


def _endpoint_name(url: str) -> str:
    if url == _TICKER_URL:
        return "company_tickers"
    if url.startswith("https://data.sec.gov/submissions/"):
        return "company_submissions"
    return "unknown"


def _normalize_symbol(symbol: str) -> str | None:
    normalized = str(symbol).strip().upper().replace(".", "-")
    return normalized if _SYMBOL.fullmatch(normalized) else None


def _utc_as_of(value: datetime | None) -> datetime:
    result = value if value is not None else datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return result.astimezone(UTC)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _MalformedSECResponse("cached SEC value must be an object")
    return value


def _mapping_value(value: object, key: str) -> int | None:
    mapping = _mapping(value)
    result = mapping.get(key)
    return result if isinstance(result, int) else None


def _ticker_mapping(payload: object, *, max_entries: int) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        raise _MalformedSECResponse("SEC ticker payload must be an object")
    if len(payload) > max_entries:
        raise _MalformedSECResponse("SEC ticker payload exceeds configured bound")
    result: dict[str, int] = {}
    for row in payload.values():
        if not isinstance(row, Mapping):
            raise _MalformedSECResponse("SEC ticker row must be an object")
        raw_ticker = row.get("ticker")
        if not isinstance(raw_ticker, str):
            raise _MalformedSECResponse("SEC ticker symbol is malformed")
        ticker = _normalize_symbol(raw_ticker)
        cik = row.get("cik_str")
        if ticker is None or isinstance(cik, bool):
            raise _MalformedSECResponse("SEC ticker row is malformed")
        try:
            parsed_cik = int(cik)
        except (TypeError, ValueError) as exc:
            raise _MalformedSECResponse("SEC ticker CIK is malformed") from exc
        if parsed_cik <= 0:
            raise _MalformedSECResponse("SEC ticker CIK must be positive")
        result[ticker] = parsed_cik
    if not result:
        raise _MalformedSECResponse("SEC ticker payload is empty")
    return result


def _latest_relevant_filing(
    payload: Mapping[str, object],
    *,
    as_of: datetime,
    freshness_days: int,
) -> _Filing | None:
    filings = payload.get("filings")
    if not isinstance(filings, Mapping):
        raise _MalformedSECResponse("SEC submissions filings are missing")
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        raise _MalformedSECResponse("SEC submissions recent filings are missing")
    forms = _required_sequence(recent, "form")
    accessions = _required_sequence(recent, "accessionNumber")
    filing_dates = _required_sequence(recent, "filingDate")
    documents = _required_sequence(recent, "primaryDocument")
    count = len(forms)
    if (
        len(accessions) != count
        or len(filing_dates) != count
        or len(documents) != count
    ):
        raise _MalformedSECResponse("SEC recent filing columns have different lengths")
    acceptance = _optional_sequence(recent, "acceptanceDateTime", count)
    threshold = (as_of - timedelta(days=freshness_days)).date()
    candidates: list[_Filing] = []
    for index in range(count):
        if not isinstance(forms[index], str) or not forms[index].strip():
            raise _MalformedSECResponse("SEC filing form is malformed")
        form = _recognized_form(forms[index])
        if form is None:
            continue
        if not isinstance(accessions[index], str):
            raise _MalformedSECResponse("SEC filing accession number is malformed")
        accession = str(accessions[index]).strip()
        if not accession:
            raise _MalformedSECResponse("SEC filing accession number is missing")
        if not isinstance(filing_dates[index], str):
            raise _MalformedSECResponse("SEC filing date is malformed")
        filing_date = _parse_date(filing_dates[index])
        published_at = _parse_acceptance(acceptance[index], filing_date)
        if (
            filing_date < threshold
            or filing_date > as_of.date()
            or published_at > as_of
        ):
            continue
        if not isinstance(documents[index], str) or not documents[index].strip():
            raise _MalformedSECResponse("SEC primary document is malformed")
        document = documents[index].strip()
        candidates.append(_Filing(form, accession, published_at, document))
    return max(
        candidates,
        key=lambda item: (item.published_at, item.accession_number),
        default=None,
    )


def _required_sequence(recent: Mapping[str, object], name: str) -> Sequence[object]:
    value = recent.get(name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _MalformedSECResponse(f"SEC recent filing {name} column is malformed")
    return value


def _optional_sequence(
    recent: Mapping[str, object], name: str, count: int
) -> Sequence[object]:
    value = recent.get(name)
    if value is None:
        return ("",) * count
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != count
    ):
        raise _MalformedSECResponse(f"SEC recent filing {name} column is malformed")
    return value


def _recognized_form(value: str) -> str | None:
    form = " ".join(value.strip().upper().split())
    exact = {
        "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A",
        "S-1", "S-1/A", "S-3", "S-3/A", "SC 13D", "SC 13D/A",
        "SC 13G", "SC 13G/A",
    }
    return form if form in exact or re.fullmatch(r"424B[A-Z0-9-]*(?:/A)?", form) else None


def _parse_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise _MalformedSECResponse("SEC filing date is malformed") from exc


def _parse_acceptance(value: object, filing_date: date) -> datetime:
    text = str(value).strip()
    if not text:
        return datetime.combine(filing_date, datetime.min.time(), tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _MalformedSECResponse("SEC acceptance timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=_EASTERN)
    return parsed.astimezone(UTC)


def _filing_url(cik: int, filing: _Filing) -> str | None:
    if filing.primary_document is None:
        return None
    document = filing.primary_document.lstrip("/")
    if "/" in document or "\\" in document:
        return None
    accession = filing.accession_number.replace("-", "")
    return _ARCHIVES_URL.format(cik=cik, accession=accession, document=document)


__all__ = [
    "SECEdgarCatalystProvider",
    "SECEdgarPolicy",
    "log_sec_edgar_provider_state",
]
