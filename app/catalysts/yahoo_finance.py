from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
import re
from threading import Lock
import time
from typing import Protocol
import unicodedata

import httpx

from app.catalysts.canonical import canonical_headline_event_id
from app.catalysts.models import CatalystEvidence
from app.catalysts.policy import DEFAULT_CATALYST_PRIORITY_POLICY
from app.momentum_scanner.models import CatalystStatus, CatalystType


_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,19}$")
_HEADLINE_SYMBOL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
)
_SEC_ACCESSION = re.compile(
    r"(?<!\d)(\d{10})-?(\d{2})-?(\d{6})(?!\d)"
)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class YahooFinanceNewsPolicy:
    """Freshness, latency, bounded-cache, and outage policy for Yahoo news."""

    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    cache_ttl_seconds: float = 300.0
    max_cache_entries: int = 512
    failure_cooldown_seconds: float = 60.0
    news_count: int = 20

    def __post_init__(self) -> None:
        if isinstance(self.freshness_minutes, bool) or self.freshness_minutes < 0:
            raise ValueError("Yahoo freshness_minutes must not be negative")
        for name in (
            "timeout_seconds",
            "cache_ttl_seconds",
            "failure_cooldown_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Yahoo {name} must be positive")
        for name in ("max_cache_entries", "news_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Yahoo {name} must be a positive integer")


class YahooFinanceNewsTransport(Protocol):
    """Isolates Yahoo's public structured, but undocumented, search API."""

    def fetch_news(self, symbol: str) -> object: ...


class YahooFinanceSearchTransport:
    """Read Yahoo's structured search JSON; never fetch or parse rendered HTML.

    Yahoo does not document a supported no-credential news API. Keeping this
    endpoint behind ``YahooFinanceNewsTransport`` limits replacement cost if
    Yahoo changes or retires the public JSON response.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        news_count: int = 20,
        client: object | None = None,
    ) -> None:
        self._news_count = news_count
        self._client = client if client is not None else httpx.Client(
            headers={"Accept": "application/json", "User-Agent": "WebullAITrader/0.1"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )

    def fetch_news(self, symbol: str) -> object:
        response = self._client.get(
            _SEARCH_URL,
            params={
                "q": symbol,
                # Quote search metadata is used only for company identity aliases.
                # No quote, price, volume, or market-data field is consumed.
                "quotesCount": 5,
                "newsCount": self._news_count,
                "enableFuzzyQuery": "false",
                "region": "US",
                "lang": "en-US",
            },
        )
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            raise YahooFinanceUnavailable("missing HTTP status")
        if status != 200:
            raise YahooFinanceUnavailable(f"HTTP {status}", status_code=status)
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise MalformedYahooFinanceResponse("invalid JSON") from exc


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    payload: object
    identity: _CompanyIdentity
    expires_at: float


@dataclass(frozen=True, slots=True)
class _Headline:
    title: str
    published_at: datetime
    source_url: str
    provider_event_id: str | None
    related_tickers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CompanyIdentity:
    symbol: str
    aliases: tuple[str, ...]


class MalformedYahooFinanceResponse(ValueError):
    pass


class YahooFinanceUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class YahooFinanceCatalystProvider:
    """Headline-only catalyst evidence; never supplies trading market data."""

    name = "YAHOO_FINANCE"

    def __init__(
        self,
        policy: YahooFinanceNewsPolicy = YahooFinanceNewsPolicy(),
        *,
        transport: YahooFinanceNewsTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, YahooFinanceNewsPolicy):
            raise TypeError("policy must be YahooFinanceNewsPolicy")
        self._policy = policy
        self._transport = transport or YahooFinanceSearchTransport(
            timeout_seconds=policy.timeout_seconds,
            news_count=policy.news_count,
        )
        self._monotonic = monotonic
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._unavailable_until = 0.0
        log_yahoo_finance_provider_state(enabled=True)

    def get_evidence(
        self, symbol: str, as_of: datetime | None = None
    ) -> CatalystEvidence:
        normalized = _normalize_symbol(symbol)
        if normalized is None:
            return self._negative(str(symbol).strip().upper() or "UNKNOWN", CatalystStatus.FALSE)
        now = _utc_as_of(as_of)
        try:
            payload, identity = self._payload(normalized)
            headlines = _parse_headlines(payload)
        except MalformedYahooFinanceResponse:
            return self._negative(normalized, CatalystStatus.UNKNOWN)
        except YahooFinanceUnavailable:
            _LOGGER.warning(
                "yahoo_finance_event event=provider_unavailable symbol=%s", normalized
            )
            return self._negative(normalized, CatalystStatus.UNAVAILABLE)

        related = tuple(item for item in headlines if normalized in item.related_tickers)
        cutoff = now - timedelta(minutes=self._policy.freshness_minutes)
        fresh = tuple(
            item for item in related if cutoff <= item.published_at <= now
        )
        _LOGGER.info(
            "yahoo_finance_event event=fresh_headline_count symbol=%s count=%s",
            normalized,
            len(fresh),
        )
        positives: list[tuple[CatalystType, _Headline]] = []
        for item in fresh:
            if not _headline_names_subject(item.title, identity):
                continue
            catalyst_type = classify_yahoo_headline(item.title)
            if catalyst_type is not None:
                positives.append((catalyst_type, item))
        if not positives:
            return self._negative(normalized, CatalystStatus.FALSE)
        catalyst_type, selected = min(
            positives,
            key=lambda pair: (
                -DEFAULT_CATALYST_PRIORITY_POLICY.priority(pair[0]),
                -int(pair[1].published_at.timestamp() * 1_000_000),
                pair[1].title.casefold(),
            ),
        )
        _LOGGER.info(
            "yahoo_finance_event event=positive_catalyst_found symbol=%s catalyst_type=%s",
            normalized,
            catalyst_type.value,
        )
        return CatalystEvidence(
            symbol=normalized,
            catalyst_type=catalyst_type,
            status=CatalystStatus.TRUE,
            headline=selected.title,
            source=self.name,
            published_at=selected.published_at,
            source_url=selected.source_url,
            provider_event_id=selected.provider_event_id,
            canonical_event_id=_canonical_event_id(
                normalized,
                catalyst_type,
                selected.title,
                selected.published_at,
                selected.source_url,
            ),
        )

    def _payload(self, symbol: str) -> tuple[object, _CompanyIdentity]:
        with self._lock:
            now = self._monotonic()
            cached = self._cache.get(symbol)
            if cached is not None and cached.expires_at > now:
                self._cache.move_to_end(symbol)
                _log_cache(symbol, hit=True)
                return cached.payload, cached.identity
            if cached is not None:
                del self._cache[symbol]
            _log_cache(symbol, hit=False)
            if now < self._unavailable_until:
                raise YahooFinanceUnavailable("provider cooldown")
            try:
                payload = self._transport.fetch_news(symbol)
            except MalformedYahooFinanceResponse:
                _log_request_failure(symbol, "malformed", None, None)
                raise
            except YahooFinanceUnavailable as exc:
                self._unavailable_until = now + self._policy.failure_cooldown_seconds
                _log_request_failure(symbol, "http", exc.status_code, type(exc).__name__)
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._unavailable_until = now + self._policy.failure_cooldown_seconds
                _log_request_failure(symbol, "network", None, type(exc).__name__)
                raise YahooFinanceUnavailable("network failure") from exc
            except Exception as exc:
                self._unavailable_until = now + self._policy.failure_cooldown_seconds
                _log_request_failure(symbol, "transport", None, type(exc).__name__)
                raise YahooFinanceUnavailable("transport failure") from exc
            _LOGGER.info(
                "yahoo_finance_event event=request_success symbol=%s", symbol
            )
            identity = _parse_company_identity(payload, symbol)
            self._cache[symbol] = _CacheEntry(
                payload,
                identity,
                now + self._policy.cache_ttl_seconds,
            )
            self._cache.move_to_end(symbol)
            while len(self._cache) > self._policy.max_cache_entries:
                self._cache.popitem(last=False)
            return payload, identity

    def _negative(self, symbol: str, status: CatalystStatus) -> CatalystEvidence:
        return CatalystEvidence(
            symbol=symbol,
            catalyst_type=CatalystType.NONE,
            status=status,
            source=self.name,
        )


_GENERIC_OR_EDITORIAL = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bmarket (?:recap|summary|update|wrap)\b",
        r"\b(?:stocks?|tickers?) to watch\b",
        r"\bwhy .* stock (?:rose|fell|jumped|dropped|is (?:up|down))\b",
        r"\btechnical analysis\b|\bchart analysis\b",
        r"\b(?:top|best) \d+ .*stocks?\b",
        r"\bshould you (?:buy|sell|invest)\b",
        r"\bopinion\b|\bwhat investors should know\b",
        r"\bearnings (?:preview|call|call transcript|date|scheduled)\b|"
        r"\b(?:will|expected to) report earnings\b",
        r"\b(?:rumou?r|reportedly|could|may|might)\b.*\b(?:acquire|acquisition|merge|merger|buyout)\b",
    )
)

_CLASSIFIERS: tuple[tuple[CatalystType, tuple[re.Pattern[str], ...]], ...] = (
    (CatalystType.EARNINGS, tuple(map(re.compile, (
        r"\bearnings\b.*\b(?:reports?|results?|beats?|misses?)\b",
        r"\b(?:reports?|announces?|posts?)\b.*\bearnings\b",
        r"\b(?:reports?|announces?|posts?)\b.*\b(?:q[1-4]|quarter(?:ly)?|annual|full[- ]year) results?\b",
        r"\b(?:q[1-4]|quarter(?:ly)?|annual|full[- ]year) results?\b",
    ), (re.IGNORECASE,) * 4))),
    (CatalystType.GUIDANCE, tuple(re.compile(p, re.IGNORECASE) for p in (
        r"\b(?:raises?|cuts?|lowers?|reaffirms?|issues?|updates?)\b.*\b(?:guidance|outlook)\b",
        r"\b(?:guidance|outlook)\b.*\b(?:raised|cut|lowered|reaffirmed|issued|updated)\b",
    ))),
    (CatalystType.FDA, tuple(re.compile(p, re.IGNORECASE) for p in (
        r"\bFDA\b.*\b(?:approves?|grants? approval|clears?|authorizes?)\b",
        r"\b(?:receives?|wins?|granted)\b.*\bFDA\b.*\b(?:approval|clearance|authorization)\b",
    ))),
    (CatalystType.CLINICAL_TRIAL, tuple(re.compile(p, re.IGNORECASE) for p in (
        r"\b(?:clinical|phase [123]) (?:trial|study)\b.*\b(?:results?|data|meets?|achieves?|endpoint|enrollment)\b",
        r"\b(?:results?|data|meets?|achieves?)\b.*\b(?:clinical|phase [123]) (?:trial|study)\b",
    ))),
    (CatalystType.ACQUISITION, tuple(re.compile(p, re.IGNORECASE) for p in (
        r"\bto acquire\b|\bacquires?\b|\bto be acquired\b",
        r"\b(?:announces?|agrees? to|completes?|closes?)\b.*\b(?:acquisition|merger|buyout)\b",
        r"\bmerges? with\b",
    ))),
    (CatalystType.CONTRACT, tuple(re.compile(p, re.IGNORECASE) for p in (
        r"\b(?:wins?|awarded|receives?|secures?)\b.*\b(?:contract|purchase order|order|award)\b",
        r"\b(?:contract|purchase order)\b.*\b(?:awarded|signed|secured|received)\b",
    ))),
    (CatalystType.PARTNERSHIP, (re.compile(
        r"\b(?:announces?|enters?|forms?|signs?)\b.*\b(?:partnership|collaboration|alliance)\b|"
        r"\b(?:partners?|collaborates?) with\b",
        re.IGNORECASE,
    ),)),
    (CatalystType.SEC_FILING, (re.compile(
        r"\bSEC filing\b|\bfiles?\b.*\b(?:8-K|10-Q|10-K|S-1|S-3|13D|13G)\b",
        re.IGNORECASE,
    ),)),
)

_STRONG_OTHER = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:declares?|increases?|raises?)\b.*\bdividend\b",
    r"\b(?:patent granted|granted (?:a )?patent)\b",
    r"\b(?:launches?|receives?)\b.*\b(?:product|certification)\b",
))


def classify_yahoo_headline(headline: str) -> CatalystType | None:
    """Return a strong conservative classification, or ``None`` to reject."""

    normalized = " ".join(str(headline).split())
    if not normalized or any(pattern.search(normalized) for pattern in _GENERIC_OR_EDITORIAL):
        return None
    for catalyst_type, patterns in _CLASSIFIERS:
        if any(pattern.search(normalized) for pattern in patterns):
            return catalyst_type
    if any(pattern.search(normalized) for pattern in _STRONG_OTHER):
        return CatalystType.OTHER
    return None


def _parse_headlines(payload: object) -> tuple[_Headline, ...]:
    if not isinstance(payload, Mapping) or "news" not in payload:
        raise MalformedYahooFinanceResponse("missing news collection")
    rows = payload["news"]
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise MalformedYahooFinanceResponse("invalid news collection")
    parsed: list[_Headline] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise MalformedYahooFinanceResponse("invalid news item")
        try:
            title = str(row["title"]).strip()
            published_raw = row["providerPublishTime"]
            source_url = str(row["link"]).strip()
            related_raw = row["relatedTickers"]
        except KeyError as exc:
            raise MalformedYahooFinanceResponse("missing news field") from exc
        if (
            not title
            or not source_url.startswith(("https://", "http://"))
            or isinstance(published_raw, bool)
            or not isinstance(published_raw, (int, float))
            or not isinstance(related_raw, Sequence)
            or isinstance(related_raw, (str, bytes, bytearray))
        ):
            raise MalformedYahooFinanceResponse("invalid news field")
        try:
            published_at = datetime.fromtimestamp(float(published_raw), tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise MalformedYahooFinanceResponse("invalid publish time") from exc
        related = tuple(
            normalized
            for value in related_raw
            if (normalized := _normalize_symbol(value)) is not None
        )
        event_id = str(row.get("uuid", "")).strip() or None
        parsed.append(_Headline(title, published_at, source_url, event_id, related))
    return tuple(parsed)


_COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "ltd",
    "plc",
}


def _parse_company_identity(payload: object, symbol: str) -> _CompanyIdentity:
    """Resolve headline aliases from quote-search metadata, conservatively.

    Missing or malformed identity metadata yields symbol-only matching. This lets
    an explicit ticker headline remain eligible without allowing an unrelated
    ``relatedTickers`` association to stand in for direct subject evidence.
    """

    aliases: set[str] = set()
    if not isinstance(payload, Mapping):
        return _CompanyIdentity(symbol, ())
    quotes = payload.get("quotes")
    if not isinstance(quotes, Sequence) or isinstance(
        quotes, (str, bytes, bytearray)
    ):
        return _CompanyIdentity(symbol, ())
    for quote in quotes:
        if not isinstance(quote, Mapping):
            continue
        if _normalize_symbol(quote.get("symbol")) != symbol:
            continue
        for field in ("shortname", "longname"):
            value = quote.get(field)
            if not isinstance(value, str):
                continue
            normalized_name = _normalize_company_text(value)
            if not normalized_name:
                continue
            aliases.add(normalized_name)
            words = normalized_name.split()
            while words and words[-1] in _COMPANY_SUFFIXES:
                words.pop()
            shortened = " ".join(words)
            if len(shortened) >= 4:
                aliases.add(shortened)
        break
    return _CompanyIdentity(
        symbol,
        tuple(
            sorted(
                aliases,
                key=lambda value: (-len(value.split()), -len(value), value),
            )
        ),
    )


def _headline_names_subject(headline: str, identity: _CompanyIdentity) -> bool:
    for token in _HEADLINE_SYMBOL_TOKEN.findall(headline):
        if _normalize_symbol(token) == identity.symbol:
            return True
    normalized_headline = _normalize_company_text(headline)
    padded_headline = f" {normalized_headline} "
    return any(f" {alias} " in padded_headline for alias in identity.aliases)


def _normalize_company_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_value = ascii_value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value).split())


def _normalize_symbol(value: object) -> str | None:
    normalized = str(value).strip().upper().replace(".", "-").replace("/", "-")
    return normalized if _SYMBOL.fullmatch(normalized) else None


def _canonical_event_id(
    symbol: str,
    catalyst_type: CatalystType,
    headline: str,
    published_at: datetime,
    source_url: str,
) -> str:
    if catalyst_type is CatalystType.SEC_FILING:
        accession = _SEC_ACCESSION.search(headline + " " + source_url)
        if accession is not None:
            return "sec-filing:" + "-".join(accession.groups())
    return canonical_headline_event_id(
        symbol, catalyst_type, headline, published_at
    )


def _utc_as_of(value: datetime | None) -> datetime:
    result = datetime.now(UTC) if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return result.astimezone(UTC)


def log_yahoo_finance_provider_state(*, enabled: bool) -> None:
    _LOGGER.info(
        "yahoo_finance_event event=provider_state enabled=%s",
        str(enabled).lower(),
    )


def _log_cache(symbol: str, *, hit: bool) -> None:
    _LOGGER.debug(
        "yahoo_finance_event event=cache_%s symbol=%s",
        "hit" if hit else "miss",
        symbol,
    )


def _log_request_failure(
    symbol: str, reason: str, status: int | None, error_type: str | None
) -> None:
    _LOGGER.warning(
        "yahoo_finance_event event=request_failure symbol=%s reason=%s status=%s error_type=%s",
        symbol,
        reason,
        status if status is not None else "none",
        error_type or "none",
    )


__all__ = [
    "MalformedYahooFinanceResponse",
    "YahooFinanceCatalystProvider",
    "YahooFinanceNewsPolicy",
    "YahooFinanceNewsTransport",
    "YahooFinanceSearchTransport",
    "YahooFinanceUnavailable",
    "classify_yahoo_headline",
    "log_yahoo_finance_provider_state",
]
