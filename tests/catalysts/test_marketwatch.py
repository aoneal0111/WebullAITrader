from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from threading import Barrier, Lock, Thread
import time
from types import SimpleNamespace

import httpx
import pytest

from app.catalysts import (
    DEFAULT_MARKETWATCH_FEEDS,
    CatalystAggregator,
    CatalystEvidence,
    CatalystStatus,
    CatalystType,
    MalformedMarketWatchResponse,
    MarketWatchCatalystProvider,
    MarketWatchFeedResponse,
    MarketWatchNewsPolicy,
    MarketWatchRSSFeedTransport,
    MarketWatchUnavailable,
    build_catalyst_providers,
    classify_marketwatch_headline,
    parse_marketwatch_rss,
)
from app.configuration import load_configuration


NOW = datetime(2026, 8, 20, 17, 0, tzinfo=UTC)


def rss(*items: str, ttl: int | None = 60) -> bytes:
    ttl_xml = "" if ttl is None else f"<ttl>{ttl}</ttl>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<rss version=\"2.0\"><channel><title>MarketWatch</title>{ttl_xml}"
        f"{''.join(items)}</channel></rss>"
    ).encode()


def story(
    title: str,
    *,
    guid: str | None = "mw-guid-1",
    url: str = "https://www.marketwatch.com/story/marketwatch-item",
    published_at: str = "Thu, 20 Aug 2026 16:30:00 GMT",
    description: str | None = None,
) -> str:
    guid_xml = "" if guid is None else f"<guid isPermaLink=\"false\">{escape(guid)}</guid>"
    description_xml = (
        "" if description is None else f"<description>{escape(description)}</description>"
    )
    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(url)}</link>"
        f"{guid_xml}<pubDate>{escape(published_at)}</pubDate>{description_xml}"
        "</item>"
    )


def feed_response(
    payload: bytes, *, etag: str | None = 'W/"one"'
) -> MarketWatchFeedResponse:
    return MarketWatchFeedResponse(200, payload, etag)


class CycleTransport:
    def __init__(self, *cycles: dict[str, MarketWatchFeedResponse | Exception]) -> None:
        self.cycles = cycles
        self.calls: list[tuple[str, str | None]] = []
        self._lock = Lock()

    def fetch_feed(self, feed, etag=None) -> MarketWatchFeedResponse:
        with self._lock:
            cycle_index = min(
                len(self.calls) // len(DEFAULT_MARKETWATCH_FEEDS),
                len(self.cycles) - 1,
            )
            self.calls.append((feed.name, etag))
            value = self.cycles[cycle_index][feed.name]
        if isinstance(value, Exception):
            raise value
        return value


def cycle(
    top: bytes | MarketWatchFeedResponse,
    bulletins: bytes | MarketWatchFeedResponse | Exception | None = None,
) -> dict[str, MarketWatchFeedResponse | Exception]:
    top_response = feed_response(top) if isinstance(top, bytes) else top
    selected = top if bulletins is None else bulletins
    bulletin_response = feed_response(selected) if isinstance(selected, bytes) else selected
    return {"TOP_STORIES": top_response, "BULLETINS": bulletin_response}


def provider_for(
    *cycles: dict[str, MarketWatchFeedResponse | Exception],
    elapsed: list[float] | None = None,
    transport: CycleTransport | None = None,
    **policy: object,
) -> tuple[MarketWatchCatalystProvider, CycleTransport]:
    selected = transport or CycleTransport(*cycles)
    clock = elapsed or [1.0]
    provider = MarketWatchCatalystProvider(
        MarketWatchNewsPolicy(**policy),
        transport=selected,
        monotonic=lambda: clock[0],
    )
    return provider, selected


def test_exactly_two_fixed_official_feeds() -> None:
    assert tuple((feed.name, feed.url) for feed in DEFAULT_MARKETWATCH_FEEDS) == (
        (
            "TOP_STORIES",
            "https://feeds.marketwatch.com/marketwatch/topstories/",
        ),
        (
            "BULLETINS",
            "https://feeds.marketwatch.com/marketwatch/bulletins/",
        ),
    )


def test_rss_parses_only_required_story_metadata_and_guid() -> None:
    parsed = parse_marketwatch_rss(
        rss(
            story(
                "AUTO reports quarterly earnings results",
                guid="opaque-18-char-id",
                description="AAPL appears only in ignored description text",
            )
        ),
        as_of=NOW,
    )

    assert parsed.advertised_ttl_seconds == 3_600.0
    assert len(parsed.stories) == 1
    item = parsed.stories[0]
    assert item.title == "AUTO reports quarterly earnings results"
    assert item.published_at == datetime(2026, 8, 20, 16, 30, tzinfo=UTC)
    assert item.source_url == "https://www.marketwatch.com/story/marketwatch-item"
    assert item.guid == item.provider_event_id == "opaque-18-char-id"


@pytest.mark.parametrize(
    "payload",
    (
        b"not xml",
        b"<rss/>",
        b"<feed><channel/></feed>",
        rss("<item><title>missing fields</title></item>"),
        rss(story("AUTO reports results", published_at="not a date")),
        rss(
            story(
                "AUTO reports results",
                published_at="Thu, 20 Aug 2026 17:06:00 GMT",
            )
        ),
    ),
)
def test_malformed_schema_invalid_and_future_timestamps_are_rejected(
    payload: bytes,
) -> None:
    with pytest.raises(MalformedMarketWatchResponse):
        parse_marketwatch_rss(payload, as_of=NOW)


@pytest.mark.parametrize(
    "payload",
    (
        b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss><channel/></rss>',
        b'<rss><channel><!ENTITY x "unsafe"></channel></rss>',
    ),
)
def test_dtd_and_entity_declarations_are_rejected(payload: bytes) -> None:
    with pytest.raises(MalformedMarketWatchResponse):
        parse_marketwatch_rss(payload, as_of=NOW)


def test_parser_enforces_payload_bound_independently_of_transport() -> None:
    payload = rss(story("AUTO reports quarterly results"))

    with pytest.raises(MalformedMarketWatchResponse):
        parse_marketwatch_rss(payload, as_of=NOW, max_payload_bytes=len(payload) - 1)


@pytest.mark.parametrize(
    "url",
    (
        "http://www.marketwatch.com/story/item",
        "https://evil.example/story/AUTO",
        "https://www.marketwatch.com.evil.example/story/AUTO",
        "https://user@www.marketwatch.com/story/AUTO",
    ),
)
def test_untrusted_article_links_are_rejected(url: str) -> None:
    with pytest.raises(MalformedMarketWatchResponse):
        parse_marketwatch_rss(rss(story("AUTO reports results", url=url)), as_of=NOW)


class HTTPResponse:
    def __init__(
        self,
        content: bytes = b"",
        *,
        status_code: int = 200,
        url: str = "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        headers: dict[str, str] | None = None,
        history: tuple[HTTPResponse, ...] = (),
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url
        self.history = history
        self.headers = {
            "Content-Length": str(len(content)),
            "Content-Type": "application/xml; charset=utf-8",
            **(headers or {}),
        }


class HTTPClient:
    def __init__(self, *responses: HTTPResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str]) -> HTTPResponse:
        self.calls.append((url, headers))
        return self.responses.pop(0)


def test_transport_uses_conditional_etag_and_accepts_304() -> None:
    payload = rss(story("AUTO reports quarterly earnings results"))
    client = HTTPClient(
        HTTPResponse(payload, headers={"ETag": 'W/"first"'}),
        HTTPResponse(status_code=304, headers={"ETag": 'W/"first"'}),
    )
    transport = MarketWatchRSSFeedTransport(client=client)

    first = transport.fetch_feed(DEFAULT_MARKETWATCH_FEEDS[0])
    second = transport.fetch_feed(DEFAULT_MARKETWATCH_FEEDS[0], first.etag)

    assert first == MarketWatchFeedResponse(200, payload, 'W/"first"')
    assert second == MarketWatchFeedResponse(304, None, 'W/"first"')
    assert client.calls == [
        (DEFAULT_MARKETWATCH_FEEDS[0].url, {}),
        (DEFAULT_MARKETWATCH_FEEDS[0].url, {"If-None-Match": 'W/"first"'}),
    ]


def test_transport_rejects_oversized_payload_and_untrusted_redirect() -> None:
    with pytest.raises(MalformedMarketWatchResponse):
        MarketWatchRSSFeedTransport(
            max_payload_bytes=3,
            client=HTTPClient(HTTPResponse(b"four")),
        ).fetch_feed(DEFAULT_MARKETWATCH_FEEDS[0])
    with pytest.raises(MalformedMarketWatchResponse):
        MarketWatchRSSFeedTransport(
            client=HTTPClient(HTTPResponse(b"", url="https://evil.example/feed"))
        ).fetch_feed(DEFAULT_MARKETWATCH_FEEDS[0])
    with pytest.raises(MalformedMarketWatchResponse):
        MarketWatchRSSFeedTransport(
            client=HTTPClient(
                HTTPResponse(
                    b"",
                    history=(HTTPResponse(url="https://evil.example/intermediate"),),
                )
            )
        ).fetch_feed(DEFAULT_MARKETWATCH_FEEDS[0])


def test_two_feeds_fetch_once_and_all_symbols_match_locally() -> None:
    provider, transport = provider_for(
        cycle(rss(story("AUTO reports quarterly earnings results")))
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.FALSE
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert transport.calls == [("TOP_STORIES", None), ("BULLETINS", None)]


def test_company_description_and_url_without_ticker_remain_false() -> None:
    provider, _ = provider_for(
        cycle(
            rss(
                story(
                    "Apple reports quarterly earnings results",
                    url="https://www.marketwatch.com/story/AAPL-quarterly-results",
                    description="AAPL reports quarterly earnings results",
                )
            )
        )
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.FALSE


def test_ambiguous_common_word_ticker_is_rejected() -> None:
    provider, _ = provider_for(cycle(rss(story("IT reports quarterly results"))))

    assert provider.get_evidence("IT", NOW).status is CatalystStatus.FALSE


@pytest.mark.parametrize(
    ("headline", "expected"),
    (
        ("AUTO reports quarterly earnings results", CatalystType.EARNINGS),
        ("AUTO raises full-year guidance", CatalystType.GUIDANCE),
        ("FDA approves AUTO therapy", CatalystType.FDA),
        ("AUTO clinical trial meets primary endpoint", CatalystType.CLINICAL_TRIAL),
        ("AUTO agrees to acquire Parts Inc", CatalystType.ACQUISITION),
        ("AUTO wins government contract award", CatalystType.CONTRACT),
        ("AUTO announces partnership with Parts Inc", CatalystType.PARTNERSHIP),
        ("AUTO files Form 8-K with SEC", CatalystType.SEC_FILING),
        ("AUTO declares quarterly dividend", CatalystType.OTHER),
    ),
)
def test_supported_classification(headline: str, expected: CatalystType) -> None:
    assert classify_marketwatch_headline(headline) is expected


@pytest.mark.parametrize(
    "headline",
    (
        "Market recap: AUTO finishes higher",
        "AUTO stock jumps after trading session",
        "Analyst says AUTO could report strong earnings",
        "Opinion: AUTO is a stock to own",
        "Top 10 stocks to watch including AUTO",
        "Technical analysis for AUTO shares",
        "AUTO earnings preview",
        "AUTO reportedly in talks to acquire Parts Inc",
        "AUTO may acquire Parts Inc",
        "AUTO acquisition possible, sources say",
    ),
)
def test_editorial_and_speculative_headlines_are_rejected(headline: str) -> None:
    assert classify_marketwatch_headline(headline) is None


def test_guid_then_normalized_url_deduplication_is_deterministic(caplog) -> None:
    caplog.set_level("INFO", logger="app.catalysts.marketwatch")
    with_guid = rss(story("AUTO reports quarterly earnings results", guid="same"))
    without_guid = rss(
        story(
            "AUTO raises full-year guidance",
            guid=None,
            url="https://marketwatch.com/story/item?b=2&a=1#fragment",
        )
    )
    provider, _ = provider_for(cycle(with_guid), max_items=256)
    provider.get_evidence("AUTO", NOW)
    assert "item_count=1" in "\n".join(
        record.getMessage() for record in caplog.records
    )

    parsed = parse_marketwatch_rss(without_guid, as_of=NOW).stories[0]
    assert parsed.guid is None
    assert parsed.provider_event_id == (
        "https://www.marketwatch.com/story/item?a=1&b=2"
    )


def test_rolling_snapshot_retains_prior_unique_stories() -> None:
    elapsed = [1.0]
    old = rss(story("OLD reports quarterly earnings results", guid="old"), ttl=0)
    new = rss(
        story(
            "NEW reports quarterly earnings results",
            guid="new",
            url="https://www.marketwatch.com/story/new",
        ),
        ttl=0,
    )
    provider, transport = provider_for(
        cycle(old),
        cycle(new),
        elapsed=elapsed,
        refresh_ttl_seconds=10.0,
    )

    assert provider.get_evidence("OLD", NOW).status is CatalystStatus.TRUE
    elapsed[0] = 11.1
    assert provider.get_evidence("NEW", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OLD", NOW).status is CatalystStatus.TRUE
    assert len(transport.calls) == 4


def test_story_expiry_is_pruned_on_successful_refresh() -> None:
    elapsed = [1.0]
    old = rss(story("OLD reports quarterly earnings results", guid="old"), ttl=0)
    future_now = NOW + timedelta(days=2)
    current = rss(
        story(
            "NEW reports quarterly earnings results",
            guid="new",
            url="https://www.marketwatch.com/story/new",
            published_at="Sat, 22 Aug 2026 16:30:00 GMT",
        ),
        ttl=0,
    )
    provider, _ = provider_for(
        cycle(old), cycle(current), elapsed=elapsed, refresh_ttl_seconds=10.0
    )

    assert provider.get_evidence("OLD", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OLD", future_now).status is CatalystStatus.FALSE
    assert provider.get_evidence("NEW", future_now).status is CatalystStatus.TRUE


def test_advertised_ttl_controls_refresh_interval() -> None:
    elapsed = [1.0]
    payload = rss(story("AUTO reports quarterly earnings results"), ttl=2)
    provider, transport = provider_for(
        cycle(payload), cycle(payload), elapsed=elapsed, refresh_ttl_seconds=10.0
    )

    provider.get_evidence("AUTO", NOW)
    elapsed[0] = 120.9
    provider.get_evidence("AUTO", NOW)
    assert len(transport.calls) == 2
    elapsed[0] = 121.1
    provider.get_evidence("AUTO", NOW)
    assert len(transport.calls) == 4


def test_advertised_ttl_survives_etag_304_refresh() -> None:
    elapsed = [1.0]
    payload = rss(story("AUTO reports quarterly results"), ttl=2)
    initial = cycle(feed_response(payload, etag="e1"))
    not_modified = {
        "TOP_STORIES": MarketWatchFeedResponse(304, None, "e1"),
        "BULLETINS": MarketWatchFeedResponse(304, None, "e1"),
    }
    provider, transport = provider_for(
        initial,
        not_modified,
        not_modified,
        elapsed=elapsed,
        refresh_ttl_seconds=10.0,
    )

    provider.get_evidence("AUTO", NOW)
    elapsed[0] = 121.1
    provider.get_evidence("AUTO", NOW)
    elapsed[0] = 130.0
    provider.get_evidence("AUTO", NOW)
    assert len(transport.calls) == 4


def test_advertised_ttl_beyond_snapshot_age_is_unknown() -> None:
    payload = rss(story("AUTO reports quarterly results"), ttl=3)
    provider, _ = provider_for(
        cycle(payload),
        refresh_ttl_seconds=10.0,
        maximum_snapshot_age_seconds=120.0,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


def test_etag_304_reuses_each_complete_feed_state() -> None:
    elapsed = [1.0]
    initial = cycle(
        feed_response(rss(story("AUTO reports quarterly results"), ttl=0), etag="e1")
    )
    not_modified = {
        "TOP_STORIES": MarketWatchFeedResponse(304, None, "e1"),
        "BULLETINS": MarketWatchFeedResponse(304, None, "e1"),
    }
    provider, transport = provider_for(
        initial,
        not_modified,
        elapsed=elapsed,
        refresh_ttl_seconds=10.0,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    elapsed[0] = 11.1
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert transport.calls[-2:] == [("TOP_STORIES", "e1"), ("BULLETINS", "e1")]


def test_partial_refresh_failure_never_produces_false() -> None:
    elapsed = [1.0]
    first = cycle(rss(story("AUTO reports quarterly earnings results"), ttl=0))
    partial = cycle(
        rss(story("OTHER reports quarterly earnings results"), ttl=0),
        httpx.ConnectError("offline"),
    )
    provider, _ = provider_for(
        first, partial, elapsed=elapsed, refresh_ttl_seconds=10.0
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    elapsed[0] = 11.1
    assert provider.get_evidence("MISSING", NOW).status is CatalystStatus.UNAVAILABLE


def test_stale_complete_304_snapshot_is_unavailable() -> None:
    later = NOW + timedelta(days=2)
    initial = cycle(
        feed_response(rss(story("AUTO reports quarterly results"), ttl=0), etag="e1")
    )
    not_modified = {
        "TOP_STORIES": MarketWatchFeedResponse(304, None, "e1"),
        "BULLETINS": MarketWatchFeedResponse(304, None, "e1"),
    }
    provider, _ = provider_for(initial, not_modified, refresh_ttl_seconds=10.0)

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("AUTO", later).status is CatalystStatus.UNAVAILABLE


def test_http_200_with_only_stale_items_is_unavailable_not_false() -> None:
    stale = rss(
        story(
            "AUTO reports quarterly results",
            published_at="Wed, 18 Aug 2026 16:30:00 GMT",
        )
    )
    provider, _ = provider_for(cycle(stale))

    assert provider.get_evidence("MISSING", NOW).status is CatalystStatus.UNAVAILABLE


def test_malformed_refresh_is_unknown_and_cooldown_prevents_retries() -> None:
    elapsed = [1.0]
    provider, transport = provider_for(
        cycle(b"not xml"), elapsed=elapsed, failure_cooldown_seconds=300.0
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN
    elapsed[0] = 100.0
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.UNKNOWN
    assert len(transport.calls) == 1


def test_concurrent_refresh_is_single_flight() -> None:
    class SlowTransport(CycleTransport):
        def fetch_feed(self, feed, etag=None) -> MarketWatchFeedResponse:
            time.sleep(0.01)
            return super().fetch_feed(feed, etag)

    transport = SlowTransport(
        cycle(rss(story("AUTO reports quarterly earnings results")))
    )
    provider, _ = provider_for(transport=transport)
    barrier = Barrier(8)
    statuses: list[CatalystStatus] = []

    def lookup() -> None:
        barrier.wait()
        statuses.append(provider.get_evidence("AUTO", NOW).status)

    threads = [Thread(target=lookup) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [CatalystStatus.TRUE] * 8
    assert len(transport.calls) == 2


def test_bounded_snapshot_keeps_newest_story() -> None:
    payload = rss(
        story(
            "OLD reports quarterly results",
            guid="old",
            published_at="Thu, 20 Aug 2026 15:30:00 GMT",
        ),
        story(
            "NEW reports quarterly results",
            guid="new",
            url="https://www.marketwatch.com/story/new",
        ),
    )
    provider, _ = provider_for(cycle(payload), max_items=1)

    assert provider.get_evidence("NEW", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OLD", NOW).status is CatalystStatus.FALSE


@dataclass
class StaticProvider:
    name: str
    evidence: CatalystEvidence

    def get_evidence(self, symbol: str, as_of=None) -> CatalystEvidence:
        return self.evidence


def test_provider_order_is_independent_with_all_existing_source_names() -> None:
    marketwatch, _ = provider_for(
        cycle(rss(story("AUTO reports quarterly earnings results")))
    )
    static = tuple(
        StaticProvider(
            source,
            CatalystEvidence(
                symbol="AUTO",
                catalyst_type=CatalystType.OTHER,
                status=CatalystStatus.TRUE,
                headline=f"AUTO declares {source} dividend",
                source=source,
                published_at=NOW - timedelta(minutes=1),
                provider_event_id=source,
                canonical_event_id=f"separate:{source}",
            ),
        )
        for source in ("WEBULL_EARNINGS", "SEC_EDGAR", "YAHOO_FINANCE", "CNBC")
    )
    providers = (marketwatch, *static)

    first = CatalystAggregator(providers).aggregate_result("AUTO", NOW)
    reversed_result = CatalystAggregator(reversed(providers)).aggregate_result(
        "AUTO", NOW
    )

    assert first == reversed_result


def test_configuration_is_opt_in_and_composes_policy() -> None:
    disabled = load_configuration({})
    enabled = load_configuration(
        {
            "MARKETWATCH_NEWS_ENABLED": "true",
            "MARKETWATCH_NEWS_FRESHNESS_MINUTES": "720",
            "MARKETWATCH_NEWS_TIMEOUT_SECONDS": "2.5",
            "MARKETWATCH_NEWS_REFRESH_TTL_SECONDS": "1800",
            "MARKETWATCH_NEWS_FAILURE_COOLDOWN_SECONDS": "600",
            "MARKETWATCH_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS": "3600",
            "MARKETWATCH_NEWS_MAX_ITEMS": "128",
            "MARKETWATCH_NEWS_MAX_PAYLOAD_BYTES": "125000",
        }
    )

    assert disabled.marketwatch_news is None
    assert enabled.marketwatch_news is not None
    assert enabled.marketwatch_news.freshness_minutes == 720
    assert enabled.marketwatch_news.timeout_seconds == 2.5
    assert enabled.marketwatch_news.refresh_ttl_seconds == 1800.0
    assert enabled.marketwatch_news.failure_cooldown_seconds == 600.0
    assert enabled.marketwatch_news.maximum_snapshot_age_seconds == 3600.0
    assert enabled.marketwatch_news.max_items == 128
    assert enabled.marketwatch_news.max_payload_bytes == 125000
    providers = build_catalyst_providers(SimpleNamespace(), enabled)
    assert tuple(item.name for item in providers) == (
        "WEBULL_EARNINGS_SEC",
        "MARKETWATCH",
    )


def test_observability_omits_payload_and_exception_text(caplog) -> None:
    caplog.set_level("DEBUG", logger="app.catalysts.marketwatch")
    secret = "private payload token"
    provider, _ = provider_for(
        cycle(httpx.ConnectError(secret), httpx.ConnectError(secret))
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=provider_state enabled=true" in messages
    assert "event=cache_miss scope=feed_snapshot" in messages
    assert "event=refresh_failure reason=network" in messages
    assert "event=provider_unavailable symbol=AUTO" in messages
    assert secret not in messages
