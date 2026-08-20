from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging
import math
import re
from threading import Lock
import time
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import xml.etree.ElementTree as ET

import httpx

from app.catalysts.canonical import canonical_headline_event_id
from app.catalysts.company_identity import (
    CompanyIdentity,
    headline_names_direct_subject,
    normalize_symbol,
)
from app.catalysts.headline_classification import classify_marketwatch_headline
from app.catalysts.models import CatalystEvidence
from app.catalysts.policy import DEFAULT_CATALYST_PRIORITY_POLICY
from app.momentum_scanner.models import CatalystStatus, CatalystType


_OFFICIAL_FEED_URLS = {
    "TOP_STORIES": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "BULLETINS": "https://feeds.marketwatch.com/marketwatch/bulletins/",
}
_TRUSTED_FEED_HOSTS = {"feeds.marketwatch.com", "feeds.content.dowjones.io"}
_TRUSTED_ARTICLE_HOSTS = {"marketwatch.com", "www.marketwatch.com"}
_SEC_ACCESSION = re.compile(r"(?<!\d)(\d{10})-?(\d{2})-?(\d{6})(?!\d)")
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MarketWatchFeed:
    name: str
    url: str

    def __post_init__(self) -> None:
        name = self.name.strip().upper()
        url = self.url.strip()
        if _OFFICIAL_FEED_URLS.get(name) != url:
            raise ValueError("MarketWatch feed must be one of the fixed official feeds")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "url", url)


DEFAULT_MARKETWATCH_FEEDS = tuple(
    MarketWatchFeed(name, url) for name, url in _OFFICIAL_FEED_URLS.items()
)


@dataclass(frozen=True, slots=True)
class MarketWatchNewsPolicy:
    """Freshness, bounded retention, latency, and outage policy for RSS."""

    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    refresh_ttl_seconds: float = 3_600.0
    failure_cooldown_seconds: float = 300.0
    maximum_snapshot_age_seconds: float = 7_200.0
    max_items: int = 256
    max_payload_bytes: int = 250_000

    def __post_init__(self) -> None:
        if isinstance(self.freshness_minutes, bool) or self.freshness_minutes < 0:
            raise ValueError("MarketWatch freshness_minutes must not be negative")
        for name in (
            "timeout_seconds",
            "refresh_ttl_seconds",
            "failure_cooldown_seconds",
            "maximum_snapshot_age_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"MarketWatch {name} must be positive")
        if self.maximum_snapshot_age_seconds < self.refresh_ttl_seconds:
            raise ValueError(
                "MarketWatch maximum_snapshot_age_seconds must cover refresh_ttl_seconds"
            )
        for name in ("max_items", "max_payload_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"MarketWatch {name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class MarketWatchStory:
    title: str
    published_at: datetime
    source_url: str
    guid: str | None

    @property
    def provider_event_id(self) -> str:
        return self.guid or self.source_url


@dataclass(frozen=True, slots=True)
class MarketWatchFeedResponse:
    status_code: int
    payload: bytes | None
    etag: str | None = None

    def __post_init__(self) -> None:
        if self.status_code not in {200, 304}:
            raise ValueError("MarketWatch feed response status must be 200 or 304")
        if self.status_code == 200 and not isinstance(self.payload, bytes):
            raise ValueError("MarketWatch HTTP 200 response requires bytes")
        if self.status_code == 304 and self.payload is not None:
            raise ValueError("MarketWatch HTTP 304 response must not include a payload")


@dataclass(frozen=True, slots=True)
class _ParsedFeed:
    stories: tuple[MarketWatchStory, ...]
    advertised_ttl_seconds: float


@dataclass(frozen=True, slots=True)
class _FeedState:
    stories: tuple[MarketWatchStory, ...]
    etag: str | None
    advertised_ttl_seconds: float


@dataclass(frozen=True, slots=True)
class _Snapshot:
    stories: tuple[MarketWatchStory, ...]
    feed_latest: tuple[tuple[str, datetime], ...]
    expires_at: float
    stale_at: float


class MalformedMarketWatchResponse(ValueError):
    pass


class MarketWatchUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MarketWatchNewsTransport(Protocol):
    def fetch_feed(
        self, feed: MarketWatchFeed, etag: str | None = None
    ) -> MarketWatchFeedResponse: ...


class MarketWatchRSSFeedTransport:
    """Fetch only the fixed public MarketWatch RSS endpoints."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_payload_bytes: int = 250_000,
        client: object | None = None,
    ) -> None:
        self._max_payload_bytes = max_payload_bytes
        self._client = client if client is not None else httpx.Client(
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9",
                "User-Agent": "WebullAITrader/0.1",
            },
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )

    def fetch_feed(
        self, feed: MarketWatchFeed, etag: str | None = None
    ) -> MarketWatchFeedResponse:
        if feed not in DEFAULT_MARKETWATCH_FEEDS:
            raise ValueError("unrecognized MarketWatch feed")
        request_headers = {"If-None-Match": etag} if etag else {}
        response = self._client.get(feed.url, headers=request_headers)
        _validate_feed_response_chain(response)
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            raise MarketWatchUnavailable("missing HTTP status")
        headers = getattr(response, "headers", {})
        response_etag = _header(headers, "ETag") or etag
        if status == 304:
            return MarketWatchFeedResponse(304, None, response_etag)
        if status != 200:
            raise MarketWatchUnavailable(f"HTTP {status}", status_code=status)
        content_type = (_header(headers, "Content-Type") or "").casefold()
        if content_type and not any(
            allowed in content_type
            for allowed in ("application/xml", "application/rss+xml", "text/xml")
        ):
            raise MalformedMarketWatchResponse(
                "MarketWatch RSS content type is unrecognized"
            )
        raw_length = _header(headers, "Content-Length")
        if raw_length is not None:
            try:
                parsed_length = int(raw_length)
                if parsed_length < 0:
                    raise ValueError
            except ValueError as exc:
                raise MalformedMarketWatchResponse(
                    "MarketWatch Content-Length is malformed"
                ) from exc
            if parsed_length > self._max_payload_bytes:
                raise MalformedMarketWatchResponse(
                    "MarketWatch RSS payload exceeds configured bound"
                )
        payload = getattr(response, "content", None)
        if not isinstance(payload, bytes):
            raise MalformedMarketWatchResponse(
                "MarketWatch RSS response body is malformed"
            )
        if len(payload) > self._max_payload_bytes:
            raise MalformedMarketWatchResponse(
                "MarketWatch RSS payload exceeds configured bound"
            )
        return MarketWatchFeedResponse(200, payload, response_etag)


class MarketWatchCatalystProvider:
    """Official-RSS headline evidence; never supplies trading market data."""

    name = "MARKETWATCH"

    def __init__(
        self,
        policy: MarketWatchNewsPolicy = MarketWatchNewsPolicy(),
        *,
        transport: MarketWatchNewsTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, MarketWatchNewsPolicy):
            raise TypeError("policy must be MarketWatchNewsPolicy")
        self._policy = policy
        self._feeds = DEFAULT_MARKETWATCH_FEEDS
        self._transport = transport or MarketWatchRSSFeedTransport(
            timeout_seconds=policy.timeout_seconds,
            max_payload_bytes=policy.max_payload_bytes,
        )
        self._monotonic = monotonic
        self._feed_states: Mapping[str, _FeedState] = {}
        self._snapshot: _Snapshot | None = None
        self._refresh_lock = Lock()
        self._failure_until = 0.0
        self._failure_status: CatalystStatus | None = None
        log_marketwatch_provider_state(enabled=True)

    @property
    def feed_urls(self) -> tuple[str, str]:
        return self._feeds[0].url, self._feeds[1].url

    def get_evidence(
        self, symbol: str, as_of: datetime | None = None
    ) -> CatalystEvidence:
        normalized = normalize_symbol(symbol)
        if normalized is None:
            return self._negative(
                str(symbol).strip().upper() or "UNKNOWN", CatalystStatus.FALSE
            )
        now = _utc_as_of(as_of)
        try:
            stories = self._stories(now)
        except MalformedMarketWatchResponse:
            return self._negative(normalized, CatalystStatus.UNKNOWN)
        except MarketWatchUnavailable:
            _LOGGER.warning(
                "marketwatch_event event=provider_unavailable symbol=%s", normalized
            )
            return self._negative(normalized, CatalystStatus.UNAVAILABLE)

        # Deliberately symbol-only for Phase 2D. No alias or association source is used.
        identity = CompanyIdentity(normalized)
        cutoff = now - timedelta(minutes=self._policy.freshness_minutes)
        positives: list[tuple[CatalystType, MarketWatchStory]] = []
        for story in stories:
            if not cutoff <= story.published_at <= now:
                continue
            if not headline_names_direct_subject(story.title, identity):
                continue
            catalyst_type = classify_marketwatch_headline(story.title)
            if catalyst_type is not None:
                positives.append((catalyst_type, story))
        if not positives:
            return self._negative(normalized, CatalystStatus.FALSE)
        catalyst_type, selected = min(
            positives,
            key=lambda pair: (
                -DEFAULT_CATALYST_PRIORITY_POLICY.priority(pair[0]),
                -int(pair[1].published_at.timestamp() * 1_000_000),
                pair[1].provider_event_id.casefold(),
                pair[1].title.casefold(),
                pair[1].source_url,
            ),
        )
        _LOGGER.info(
            "marketwatch_event event=evidence_found symbol=%s catalyst_type=%s",
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

    def _stories(self, as_of: datetime) -> tuple[MarketWatchStory, ...]:
        now = self._monotonic()
        snapshot = self._snapshot
        if self._snapshot_usable(snapshot, now, as_of):
            _log_cache(hit=True)
            return snapshot.stories
        _log_cache(hit=False)
        self._log_stale_rejection(snapshot, now, as_of)
        with self._refresh_lock:
            now = self._monotonic()
            snapshot = self._snapshot
            if self._snapshot_usable(snapshot, now, as_of):
                _log_cache(hit=True)
                return snapshot.stories
            if now < self._failure_until:
                if self._failure_status is CatalystStatus.UNKNOWN:
                    raise MalformedMarketWatchResponse(
                        "provider cooldown after malformed RSS"
                    )
                raise MarketWatchUnavailable("provider cooldown")
            return self._refresh(now, as_of)

    def _refresh(
        self, monotonic_now: float, as_of: datetime
    ) -> tuple[MarketWatchStory, ...]:
        pending_states: dict[str, _FeedState] = {}
        advertised_ttls: list[float] = []
        try:
            for feed in self._feeds:
                prior = self._feed_states.get(feed.name)
                response = self._transport.fetch_feed(
                    feed, None if prior is None else prior.etag
                )
                if response.status_code == 304:
                    if prior is None:
                        raise MalformedMarketWatchResponse(
                            "MarketWatch returned 304 without cached feed"
                        )
                    pending_states[feed.name] = _FeedState(
                        prior.stories,
                        response.etag or prior.etag,
                        prior.advertised_ttl_seconds,
                    )
                    advertised_ttls.append(prior.advertised_ttl_seconds)
                    continue
                if response.payload is None:
                    raise MalformedMarketWatchResponse(
                        "MarketWatch HTTP 200 payload is missing"
                    )
                parsed = parse_marketwatch_rss(
                    response.payload,
                    as_of=as_of,
                    max_payload_bytes=self._policy.max_payload_bytes,
                )
                pending_states[feed.name] = _FeedState(
                    parsed.stories,
                    response.etag,
                    parsed.advertised_ttl_seconds,
                )
                advertised_ttls.append(parsed.advertised_ttl_seconds)
            self._require_fresh_complete_feed_set(pending_states, as_of)
        except MalformedMarketWatchResponse:
            self._mark_failure(monotonic_now, CatalystStatus.UNKNOWN, "malformed")
            raise
        except MarketWatchUnavailable as exc:
            self._mark_failure(
                monotonic_now, CatalystStatus.UNAVAILABLE, "http", exc.status_code
            )
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._mark_failure(monotonic_now, CatalystStatus.UNAVAILABLE, "network")
            raise MarketWatchUnavailable("network failure") from exc
        except Exception as exc:
            self._mark_failure(monotonic_now, CatalystStatus.UNAVAILABLE, "transport")
            raise MarketWatchUnavailable("transport failure") from exc

        advertised_ttl = max(advertised_ttls, default=0.0)
        if advertised_ttl > self._policy.maximum_snapshot_age_seconds:
            self._mark_failure(
                monotonic_now, CatalystStatus.UNKNOWN, "ttl_bound"
            )
            raise MalformedMarketWatchResponse(
                "MarketWatch advertised ttl exceeds maximum snapshot age"
            )
        refresh_seconds = max(self._policy.refresh_ttl_seconds, advertised_ttl)
        retained = (() if self._snapshot is None else self._snapshot.stories) + tuple(
            story
            for feed in self._feeds
            for story in pending_states[feed.name].stories
        )
        cutoff = as_of - timedelta(minutes=self._policy.freshness_minutes)
        retained = tuple(
            story
            for story in retained
            if cutoff <= story.published_at <= as_of + _MAX_FUTURE_SKEW
        )
        stories = _combined_stories(retained, max_items=self._policy.max_items)
        feed_latest = tuple(
            (feed.name, max(story.published_at for story in pending_states[feed.name].stories))
            for feed in self._feeds
        )
        snapshot = _Snapshot(
            stories=stories,
            feed_latest=feed_latest,
            expires_at=monotonic_now + refresh_seconds,
            stale_at=monotonic_now + self._policy.maximum_snapshot_age_seconds,
        )
        self._feed_states = pending_states
        self._snapshot = snapshot
        self._failure_until = 0.0
        self._failure_status = None
        _LOGGER.info(
            "marketwatch_event event=refresh_success feed_count=%s item_count=%s refresh_seconds=%s",
            len(self._feeds),
            len(stories),
            int(refresh_seconds),
        )
        return stories

    def _require_fresh_complete_feed_set(
        self, states: Mapping[str, _FeedState], as_of: datetime
    ) -> None:
        if set(states) != {feed.name for feed in self._feeds}:
            raise MarketWatchUnavailable("incomplete MarketWatch feed set")
        cutoff = as_of - timedelta(minutes=self._policy.freshness_minutes)
        for feed in self._feeds:
            stories = states[feed.name].stories
            if not stories or not any(
                cutoff <= story.published_at <= as_of + _MAX_FUTURE_SKEW
                for story in stories
            ):
                _LOGGER.warning(
                    "marketwatch_event event=stale_snapshot_rejection feed=%s reason=publication",
                    feed.name,
                )
                raise MarketWatchUnavailable("stale MarketWatch feed")

    def _snapshot_usable(
        self, snapshot: _Snapshot | None, monotonic_now: float, as_of: datetime
    ) -> bool:
        if snapshot is None:
            return False
        if snapshot.expires_at <= monotonic_now or snapshot.stale_at <= monotonic_now:
            return False
        cutoff = as_of - timedelta(minutes=self._policy.freshness_minutes)
        return len(snapshot.feed_latest) == len(self._feeds) and all(
            cutoff <= latest <= as_of + _MAX_FUTURE_SKEW
            for _, latest in snapshot.feed_latest
        )

    def _log_stale_rejection(
        self, snapshot: _Snapshot | None, monotonic_now: float, as_of: datetime
    ) -> None:
        if snapshot is None:
            return
        if snapshot.stale_at <= monotonic_now:
            reason = "maximum_age"
        elif not self._snapshot_usable(snapshot, monotonic_now, as_of):
            reason = "expired_or_publication"
        else:
            return
        _LOGGER.warning(
            "marketwatch_event event=stale_snapshot_rejection feed=combined reason=%s",
            reason,
        )

    def _mark_failure(
        self,
        now: float,
        status: CatalystStatus,
        reason: str,
        http_status: int | None = None,
    ) -> None:
        self._failure_until = now + self._policy.failure_cooldown_seconds
        self._failure_status = status
        _LOGGER.warning(
            "marketwatch_event event=refresh_failure reason=%s status=%s http_status=%s",
            reason,
            status.value,
            http_status if http_status is not None else "none",
        )

    def _negative(self, symbol: str, status: CatalystStatus) -> CatalystEvidence:
        return CatalystEvidence(
            symbol=symbol,
            catalyst_type=CatalystType.NONE,
            status=status,
            source=self.name,
        )


def parse_marketwatch_rss(
    payload: bytes,
    *,
    as_of: datetime | None = None,
    max_payload_bytes: int = 250_000,
) -> _ParsedFeed:
    if not isinstance(payload, bytes) or not payload:
        raise MalformedMarketWatchResponse("MarketWatch RSS payload is empty")
    if (
        isinstance(max_payload_bytes, bool)
        or not isinstance(max_payload_bytes, int)
        or max_payload_bytes <= 0
    ):
        raise ValueError("max_payload_bytes must be a positive integer")
    if len(payload) > max_payload_bytes:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS payload exceeds configured bound"
        )
    upper_payload = payload.upper()
    if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS declarations are not permitted"
        )
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MalformedMarketWatchResponse("MarketWatch RSS XML is malformed") from exc
    if _local_name(root.tag) != "rss":
        raise MalformedMarketWatchResponse("MarketWatch RSS root is unrecognized")
    channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
    if channel is None:
        raise MalformedMarketWatchResponse("MarketWatch RSS channel is missing")
    effective_as_of = _utc_as_of(as_of)
    stories = tuple(
        _parse_item(item, effective_as_of)
        for item in channel
        if _local_name(item.tag) == "item"
    )
    return _ParsedFeed(stories, _rss_ttl(channel))


def _parse_item(item: ET.Element, as_of: datetime) -> MarketWatchStory:
    title = _required_text(item, "title")
    source_url = _normalize_marketwatch_url(_required_text(item, "link"))
    published_at = _published_at(_required_text(item, "pubDate"))
    if published_at > as_of + _MAX_FUTURE_SKEW:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS publication timestamp is unreasonably future-dated"
        )
    return MarketWatchStory(
        title=title,
        published_at=published_at,
        source_url=source_url,
        guid=_optional_text(item, "guid"),
    )


def _combined_stories(
    stories: tuple[MarketWatchStory, ...], *, max_items: int
) -> tuple[MarketWatchStory, ...]:
    unique: dict[tuple[str, str], MarketWatchStory] = {}
    for story in stories:
        key = _deduplication_key(story)
        existing = unique.get(key)
        if existing is None or _story_tie_breaker(story) < _story_tie_breaker(existing):
            unique[key] = story
    ordered = sorted(unique.values(), key=_story_tie_breaker)
    return tuple(ordered[:max_items])


def _deduplication_key(story: MarketWatchStory) -> tuple[str, str]:
    if story.guid is not None:
        return "guid", story.guid.casefold()
    return "url", story.source_url.casefold()


def _story_tie_breaker(story: MarketWatchStory) -> tuple[object, ...]:
    return (
        -int(story.published_at.timestamp() * 1_000_000),
        story.title.casefold(),
        story.source_url,
        (story.guid or "").casefold(),
    )


def _normalize_marketwatch_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError as exc:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS article URL is malformed"
        ) from exc
    if parsed.scheme.casefold() != "https" or hostname not in _TRUSTED_ARTICLE_HOSTS:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS article URL is unrecognized"
        )
    if port not in {None, 443} or parsed.username or parsed.password:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS article URL authority is unrecognized"
        )
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse(("https", "www.marketwatch.com", parsed.path or "/", "", query, ""))


def _validate_final_feed_url(value: object) -> None:
    try:
        parsed = urlparse(str(value))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS redirect URL is malformed"
        ) from exc
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() not in _TRUSTED_FEED_HOSTS
        or port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS redirect host is untrusted"
        )


def _validate_feed_response_chain(response: object) -> None:
    history = getattr(response, "history", ())
    if not isinstance(history, (tuple, list)):
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS redirect history is malformed"
        )
    for prior_response in history:
        _validate_final_feed_url(getattr(prior_response, "url", None))
    _validate_final_feed_url(getattr(response, "url", None))


def _rss_ttl(channel: ET.Element) -> float:
    value = _optional_text(channel, "ttl")
    if value is None:
        return 0.0
    try:
        minutes = int(value)
    except ValueError as exc:
        raise MalformedMarketWatchResponse("MarketWatch RSS ttl is malformed") from exc
    if minutes < 0:
        raise MalformedMarketWatchResponse("MarketWatch RSS ttl is malformed")
    return float(minutes * 60)


def _required_text(parent: ET.Element, tag: str) -> str:
    value = _optional_text(parent, tag)
    if value is None:
        raise MalformedMarketWatchResponse(f"MarketWatch RSS {tag} is missing")
    return value


def _optional_text(parent: ET.Element, tag: str) -> str | None:
    child = next(
        (candidate for candidate in parent if _local_name(candidate.tag) == tag), None
    )
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _published_at(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS pubDate is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedMarketWatchResponse(
            "MarketWatch RSS pubDate must include a timezone"
        )
    return parsed.astimezone(UTC)


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
    return canonical_headline_event_id(symbol, catalyst_type, headline, published_at)


def _header(headers: object, name: str) -> str | None:
    if not hasattr(headers, "get"):
        return None
    value = headers.get(name)
    if value is None:
        value = headers.get(name.casefold())
    normalized = "" if value is None else str(value).strip()
    return normalized or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _utc_as_of(value: datetime | None) -> datetime:
    result = datetime.now(UTC) if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return result.astimezone(UTC)


def log_marketwatch_provider_state(*, enabled: bool) -> None:
    _LOGGER.info(
        "marketwatch_event event=provider_state enabled=%s",
        str(bool(enabled)).lower(),
    )


def _log_cache(*, hit: bool) -> None:
    _LOGGER.debug(
        "marketwatch_event event=cache_%s scope=feed_snapshot",
        "hit" if hit else "miss",
    )


__all__ = [
    "DEFAULT_MARKETWATCH_FEEDS",
    "MalformedMarketWatchResponse",
    "MarketWatchCatalystProvider",
    "MarketWatchFeed",
    "MarketWatchFeedResponse",
    "MarketWatchNewsPolicy",
    "MarketWatchNewsTransport",
    "MarketWatchRSSFeedTransport",
    "MarketWatchStory",
    "MarketWatchUnavailable",
    "classify_marketwatch_headline",
    "log_marketwatch_provider_state",
    "parse_marketwatch_rss",
]
