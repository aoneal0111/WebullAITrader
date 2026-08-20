from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging
import math
import re
from threading import Lock
import time
from typing import Protocol
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx

from app.catalysts.canonical import canonical_headline_event_id
from app.catalysts.company_identity import (
    CompanyIdentityRegistry,
    CompanyIdentityResolver,
    headline_names_direct_subject,
    normalize_symbol,
)
from app.catalysts.headline_classification import classify_cnbc_headline
from app.catalysts.models import CatalystEvidence
from app.catalysts.policy import DEFAULT_CATALYST_PRIORITY_POLICY
from app.momentum_scanner.models import CatalystStatus, CatalystType


_FEED_URL = "https://www.cnbc.com/id/{feed_id}/device/rss/rss.html"
_METADATA_NAMESPACE = "http://search.cnbc.com/rss/2.0/modules/siteContentMetadata"
_SEC_ACCESSION = re.compile(r"(?<!\d)(\d{10})-?(\d{2})-?(\d{6})(?!\d)")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CNBCFeed:
    name: str
    feed_id: int

    def __post_init__(self) -> None:
        name = self.name.strip().upper()
        if not name:
            raise ValueError("CNBC feed name is required")
        if (
            isinstance(self.feed_id, bool)
            or not isinstance(self.feed_id, int)
            or self.feed_id <= 0
        ):
            raise ValueError("CNBC feed_id must be a positive integer")
        object.__setattr__(self, "name", name)

    @property
    def url(self) -> str:
        return _FEED_URL.format(feed_id=self.feed_id)


DEFAULT_CNBC_FEEDS = (
    CNBCFeed("BUSINESS", 10001147),
    CNBCFeed("EARNINGS", 15839135),
    CNBCFeed("HEALTH_CARE", 10000108),
    CNBCFeed("TOP_NEWS", 100003114),
)


@dataclass(frozen=True, slots=True)
class CNBCNewsPolicy:
    """Freshness, latency, bounded snapshot, and outage policy for CNBC RSS."""

    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    refresh_ttl_seconds: float = 3_600.0
    failure_cooldown_seconds: float = 60.0
    maximum_snapshot_age_seconds: float = 7_200.0
    max_items: int = 512
    max_payload_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if isinstance(self.freshness_minutes, bool) or self.freshness_minutes < 0:
            raise ValueError("CNBC freshness_minutes must not be negative")
        for name in (
            "timeout_seconds",
            "refresh_ttl_seconds",
            "failure_cooldown_seconds",
            "maximum_snapshot_age_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"CNBC {name} must be positive")
        if self.maximum_snapshot_age_seconds < self.refresh_ttl_seconds:
            raise ValueError(
                "CNBC maximum_snapshot_age_seconds must cover refresh_ttl_seconds"
            )
        for name in ("max_items", "max_payload_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"CNBC {name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class CNBCStory:
    title: str
    published_at: datetime
    source_url: str
    provider_event_id: str
    metadata_id: str | None
    guid: str | None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedFeed:
    stories: tuple[CNBCStory, ...]
    advertised_ttl_seconds: float


@dataclass(frozen=True, slots=True)
class _Snapshot:
    stories: tuple[CNBCStory, ...]
    expires_at: float
    stale_at: float


class MalformedCNBCResponse(ValueError):
    pass


class CNBCUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CNBCNewsTransport(Protocol):
    def fetch_feed(self, feed: CNBCFeed) -> bytes: ...


class CNBCRSSFeedTransport:
    """Fetch only CNBC's explicitly published RSS endpoints."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_payload_bytes: int = 1_000_000,
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

    def fetch_feed(self, feed: CNBCFeed) -> bytes:
        response = self._client.get(feed.url)
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            raise CNBCUnavailable("missing HTTP status")
        if status != 200:
            raise CNBCUnavailable(f"HTTP {status}", status_code=status)
        headers = getattr(response, "headers", {})
        raw_length = headers.get("Content-Length") if hasattr(headers, "get") else None
        if raw_length is not None:
            try:
                if int(raw_length) > self._max_payload_bytes:
                    raise MalformedCNBCResponse("CNBC RSS payload exceeds configured bound")
            except ValueError as exc:
                raise MalformedCNBCResponse("CNBC Content-Length is malformed") from exc
        payload = getattr(response, "content", None)
        if not isinstance(payload, bytes):
            raise MalformedCNBCResponse("CNBC RSS response body is malformed")
        if len(payload) > self._max_payload_bytes:
            raise MalformedCNBCResponse("CNBC RSS payload exceeds configured bound")
        return payload


class CNBCCatalystProvider:
    """Official-RSS headline evidence; never supplies trading market data."""

    name = "CNBC"

    def __init__(
        self,
        policy: CNBCNewsPolicy = CNBCNewsPolicy(),
        *,
        feeds: tuple[CNBCFeed, ...] = DEFAULT_CNBC_FEEDS,
        identity_resolver: CompanyIdentityResolver | None = None,
        transport: CNBCNewsTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, CNBCNewsPolicy):
            raise TypeError("policy must be CNBCNewsPolicy")
        if not feeds or any(not isinstance(feed, CNBCFeed) for feed in feeds):
            raise ValueError("at least one valid CNBC feed is required")
        if len({feed.feed_id for feed in feeds}) != len(feeds):
            raise ValueError("CNBC feed IDs must be unique")
        resolver = identity_resolver or CompanyIdentityRegistry()
        if not isinstance(resolver, CompanyIdentityResolver):
            raise TypeError("identity_resolver must implement CompanyIdentityResolver")
        self._policy = policy
        self._feeds = tuple(feeds)
        self._identity_resolver = resolver
        self._transport = transport or CNBCRSSFeedTransport(
            timeout_seconds=policy.timeout_seconds,
            max_payload_bytes=policy.max_payload_bytes,
        )
        self._monotonic = monotonic
        self._snapshot: _Snapshot | None = None
        self._refresh_lock = Lock()
        self._failure_until = 0.0
        self._failure_status: CatalystStatus | None = None
        log_cnbc_provider_state(enabled=True)

    @property
    def feed_ids(self) -> tuple[int, ...]:
        return tuple(feed.feed_id for feed in self._feeds)

    def get_evidence(
        self, symbol: str, as_of: datetime | None = None
    ) -> CatalystEvidence:
        normalized = normalize_symbol(symbol)
        if normalized is None:
            return self._negative(str(symbol).strip().upper() or "UNKNOWN", CatalystStatus.FALSE)
        now = _utc_as_of(as_of)
        try:
            stories = self._stories()
        except MalformedCNBCResponse:
            return self._negative(normalized, CatalystStatus.UNKNOWN)
        except CNBCUnavailable:
            _LOGGER.warning("cnbc_event event=provider_unavailable symbol=%s", normalized)
            return self._negative(normalized, CatalystStatus.UNAVAILABLE)

        identity = self._identity_resolver.resolve(normalized)
        cutoff = now - timedelta(minutes=self._policy.freshness_minutes)
        positives: list[tuple[CatalystType, CNBCStory]] = []
        for story in stories:
            if not cutoff <= story.published_at <= now:
                continue
            if not headline_names_direct_subject(story.title, identity):
                continue
            catalyst_type = classify_cnbc_headline(story.title)
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
            ),
        )
        _LOGGER.info(
            "cnbc_event event=positive_catalyst_found symbol=%s catalyst_type=%s",
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

    def _stories(self) -> tuple[CNBCStory, ...]:
        now = self._monotonic()
        snapshot = self._snapshot
        if snapshot is not None and snapshot.expires_at > now and snapshot.stale_at > now:
            _log_cache(hit=True)
            return snapshot.stories
        _log_cache(hit=False)
        with self._refresh_lock:
            now = self._monotonic()
            snapshot = self._snapshot
            if snapshot is not None and snapshot.expires_at > now and snapshot.stale_at > now:
                _log_cache(hit=True)
                return snapshot.stories
            if now < self._failure_until:
                if self._failure_status is CatalystStatus.UNKNOWN:
                    raise MalformedCNBCResponse("provider cooldown after malformed RSS")
                raise CNBCUnavailable("provider cooldown")
            try:
                parsed_feeds: list[_ParsedFeed] = []
                for feed in self._feeds:
                    _LOGGER.debug(
                        "cnbc_event event=feed_refresh feed=%s feed_id=%s",
                        feed.name,
                        feed.feed_id,
                    )
                    parsed_feeds.append(
                        parse_cnbc_rss(self._transport.fetch_feed(feed))
                    )
                parsed = tuple(parsed_feeds)
            except MalformedCNBCResponse:
                self._mark_failure(now, CatalystStatus.UNKNOWN, "malformed")
                raise
            except CNBCUnavailable as exc:
                self._mark_failure(now, CatalystStatus.UNAVAILABLE, "http", exc.status_code)
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._mark_failure(now, CatalystStatus.UNAVAILABLE, "network")
                raise CNBCUnavailable("network failure") from exc
            except Exception as exc:
                self._mark_failure(now, CatalystStatus.UNAVAILABLE, "transport")
                raise CNBCUnavailable("transport failure") from exc
            stories = _combined_stories(parsed, max_items=self._policy.max_items)
            advertised_ttl = max(
                (feed.advertised_ttl_seconds for feed in parsed),
                default=0.0,
            )
            if advertised_ttl > self._policy.maximum_snapshot_age_seconds:
                self._mark_failure(now, CatalystStatus.UNKNOWN, "ttl_bound")
                raise MalformedCNBCResponse(
                    "CNBC advertised ttl exceeds maximum snapshot age"
                )
            refresh_seconds = max(
                self._policy.refresh_ttl_seconds,
                advertised_ttl,
            )
            self._snapshot = _Snapshot(
                stories=stories,
                expires_at=now + refresh_seconds,
                stale_at=now + self._policy.maximum_snapshot_age_seconds,
            )
            self._failure_until = 0.0
            self._failure_status = None
            _LOGGER.info(
                "cnbc_event event=refresh_success feed_count=%s item_count=%s refresh_seconds=%s",
                len(self._feeds),
                len(stories),
                int(refresh_seconds),
            )
            return stories

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
            "cnbc_event event=refresh_failure reason=%s status=%s http_status=%s",
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


def parse_cnbc_rss(payload: bytes) -> _ParsedFeed:
    if not isinstance(payload, bytes) or not payload:
        raise MalformedCNBCResponse("CNBC RSS payload is empty")
    upper_prefix = payload[:1_024].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise MalformedCNBCResponse("CNBC RSS declarations are not permitted")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MalformedCNBCResponse("CNBC RSS XML is malformed") from exc
    if _local_name(root.tag) != "rss":
        raise MalformedCNBCResponse("CNBC RSS root is unrecognized")
    channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
    if channel is None:
        raise MalformedCNBCResponse("CNBC RSS channel is missing")
    ttl = _rss_ttl(channel)
    stories: list[CNBCStory] = []
    for item in (child for child in channel if _local_name(child.tag) == "item"):
        story = _parse_item(item)
        if story is not None:
            stories.append(story)
    return _ParsedFeed(tuple(stories), ttl)


def _parse_item(item: ET.Element) -> CNBCStory | None:
    title = _required_text(item, "title")
    source_url = _required_text(item, "link")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme != "https" or parsed_url.hostname not in {"cnbc.com", "www.cnbc.com"}:
        raise MalformedCNBCResponse("CNBC RSS article URL is unrecognized")
    published_at = _published_at(_required_text(item, "pubDate"))
    guid = _optional_text(item, "guid")
    metadata_id = _optional_text(item, f"{{{_METADATA_NAMESPACE}}}id")
    metadata_type = _required_text(item, f"{{{_METADATA_NAMESPACE}}}type")
    sponsored = _required_text(item, f"{{{_METADATA_NAMESPACE}}}sponsored").casefold()
    if sponsored not in {"true", "false"}:
        raise MalformedCNBCResponse("CNBC sponsored flag is malformed")
    if sponsored == "true":
        return None
    if metadata_type.casefold() != "cnbcnewsstory":
        return None
    provider_event_id = metadata_id or guid
    if provider_event_id is None:
        provider_event_id = source_url
    return CNBCStory(
        title=title,
        published_at=published_at,
        source_url=source_url,
        provider_event_id=provider_event_id,
        metadata_id=metadata_id,
        guid=guid,
        description=_optional_text(item, "description"),
    )


def _combined_stories(
    parsed_feeds: tuple[_ParsedFeed, ...], *, max_items: int
) -> tuple[CNBCStory, ...]:
    unique: dict[tuple[str, str], CNBCStory] = {}
    for parsed in parsed_feeds:
        for story in parsed.stories:
            key = _deduplication_key(story)
            existing = unique.get(key)
            if existing is None or _story_tie_breaker(story) < _story_tie_breaker(existing):
                unique[key] = story
    ordered = sorted(
        unique.values(),
        key=lambda story: (
            -int(story.published_at.timestamp() * 1_000_000),
            story.provider_event_id.casefold(),
            story.title.casefold(),
            story.source_url,
        ),
    )
    return tuple(ordered[:max_items])


def _deduplication_key(story: CNBCStory) -> tuple[str, str]:
    if story.metadata_id is not None:
        return "metadata", story.metadata_id.casefold()
    if story.guid is not None:
        return "guid", story.guid.casefold()
    return "url", story.source_url.casefold()


def _story_tie_breaker(story: CNBCStory) -> tuple[object, ...]:
    return (
        -int(story.published_at.timestamp() * 1_000_000),
        story.title.casefold(),
        story.source_url,
        story.provider_event_id.casefold(),
    )


def _rss_ttl(channel: ET.Element) -> float:
    value = _optional_text(channel, "ttl")
    if value is None:
        return 0.0
    try:
        minutes = int(value)
    except ValueError as exc:
        raise MalformedCNBCResponse("CNBC RSS ttl is malformed") from exc
    if minutes < 0:
        raise MalformedCNBCResponse("CNBC RSS ttl is malformed")
    return float(minutes * 60)


def _required_text(parent: ET.Element, tag: str) -> str:
    value = _optional_text(parent, tag)
    if value is None:
        raise MalformedCNBCResponse(f"CNBC RSS {tag.rsplit('}', 1)[-1]} is missing")
    return value


def _optional_text(parent: ET.Element, tag: str) -> str | None:
    child = parent.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _published_at(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise MalformedCNBCResponse("CNBC RSS pubDate is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedCNBCResponse("CNBC RSS pubDate must include a timezone")
    return parsed.astimezone(UTC)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _utc_as_of(value: datetime | None) -> datetime:
    result = datetime.now(UTC) if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return result.astimezone(UTC)


def log_cnbc_provider_state(*, enabled: bool) -> None:
    _LOGGER.info(
        "cnbc_event event=provider_state enabled=%s",
        str(bool(enabled)).lower(),
    )


def _log_cache(*, hit: bool) -> None:
    _LOGGER.debug("cnbc_event event=cache_%s scope=feed_snapshot", "hit" if hit else "miss")


__all__ = [
    "CNBCCatalystProvider",
    "CNBCFeed",
    "CNBCNewsPolicy",
    "CNBCNewsTransport",
    "CNBCRSSFeedTransport",
    "CNBCStory",
    "CNBCUnavailable",
    "DEFAULT_CNBC_FEEDS",
    "MalformedCNBCResponse",
    "classify_cnbc_headline",
    "log_cnbc_provider_state",
    "parse_cnbc_rss",
]
