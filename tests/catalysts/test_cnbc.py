from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from itertools import permutations
from threading import Barrier, Lock, Thread
import time
from types import SimpleNamespace

import httpx
import pytest

from app.catalysts import (
    CNBCCatalystProvider,
    CNBCFeed,
    CNBCNewsPolicy,
    CNBCRSSFeedTransport,
    CNBCUnavailable,
    CatalystAggregator,
    CatalystEvidence,
    CatalystStatus,
    CatalystType,
    CompanyIdentity,
    CompanyIdentityRegistry,
    DEFAULT_CNBC_FEEDS,
    MalformedCNBCResponse,
    build_catalyst_providers,
    classify_cnbc_headline,
    parse_cnbc_rss,
)
from app.catalysts.canonical import canonical_headline_event_id
from app.configuration import load_configuration


NOW = datetime(2026, 8, 20, 17, 0, tzinfo=UTC)
METADATA = "http://search.cnbc.com/rss/2.0/modules/siteContentMetadata"


def rss(*items: str, ttl: int | None = None) -> bytes:
    ttl_xml = f"<ttl>{ttl}</ttl>" if ttl is not None else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss xmlns:metadata="{METADATA}" version="2.0"><channel>'
        f"<title>Test</title>{ttl_xml}{''.join(items)}</channel></rss>"
    ).encode()


def story(
    title: str,
    *,
    story_id: str | None = "108352151",
    guid: str | None = "108352151",
    url: str = "https://www.cnbc.com/2026/08/20/story.html",
    published_at: str = "Thu, 20 Aug 2026 16:30:01 GMT",
    description: str = "Summary",
    sponsored: str = "false",
    metadata_type: str = "cnbcnewsstory",
) -> str:
    metadata_id = (
        f"<metadata:id>{escape(story_id)}</metadata:id>"
        if story_id is not None
        else ""
    )
    guid_xml = f"<guid>{escape(guid)}</guid>" if guid is not None else ""
    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(url)}</link>"
        f"{guid_xml}{metadata_id}"
        f"<metadata:type>{escape(metadata_type)}</metadata:type>"
        f"<metadata:sponsored>{escape(sponsored)}</metadata:sponsored>"
        f"<description>{escape(description)}</description>"
        f"<pubDate>{escape(published_at)}</pubDate>"
        "</item>"
    )


class FeedTransport:
    def __init__(self, response: bytes | Exception, *later: bytes | Exception) -> None:
        self.responses = [response, *later]
        self.calls: list[CNBCFeed] = []
        self._lock = Lock()

    def fetch_feed(self, feed: CNBCFeed) -> bytes:
        with self._lock:
            index = len(self.calls)
            self.calls.append(feed)
            cycle = min(index // len(DEFAULT_CNBC_FEEDS), len(self.responses) - 1)
            value = self.responses[cycle]
        if isinstance(value, Exception):
            raise value
        return value


def identities(*values: CompanyIdentity) -> CompanyIdentityRegistry:
    return CompanyIdentityRegistry(values)


def provider_for(
    response: bytes | Exception,
    *,
    registry: CompanyIdentityRegistry | None = None,
    elapsed: list[float] | None = None,
    transport: FeedTransport | None = None,
    **policy: object,
) -> tuple[CNBCCatalystProvider, FeedTransport]:
    selected_transport = transport or FeedTransport(response)
    clock = elapsed or [1.0]
    provider = CNBCCatalystProvider(
        CNBCNewsPolicy(**policy),
        identity_resolver=registry,
        transport=selected_transport,
        monotonic=lambda: clock[0],
    )
    return provider, selected_transport


def test_valid_rss_parses_timezone_story_id_guid_and_url() -> None:
    parsed = parse_cnbc_rss(
        rss(story("Auto Corp reports quarterly results"), ttl=60)
    )

    assert parsed.advertised_ttl_seconds == 3_600.0
    assert len(parsed.stories) == 1
    item = parsed.stories[0]
    assert item.title == "Auto Corp reports quarterly results"
    assert item.published_at == datetime(2026, 8, 20, 16, 30, 1, tzinfo=UTC)
    assert item.source_url == "https://www.cnbc.com/2026/08/20/story.html"
    assert item.provider_event_id == "108352151"
    assert item.metadata_id == item.guid == "108352151"


def test_story_id_precedes_guid_and_guid_precedes_url() -> None:
    parsed = parse_cnbc_rss(
        rss(
            story("AUTO reports Q2 results", story_id="meta", guid="guid"),
            story(
                "AUTO raises guidance",
                story_id=None,
                guid="guid-only",
                url="https://www.cnbc.com/2026/08/20/guid.html",
            ),
            story(
                "AUTO wins contract",
                story_id=None,
                guid=None,
                url="https://www.cnbc.com/2026/08/20/url.html",
            ),
        )
    )

    assert tuple(item.provider_event_id for item in parsed.stories) == (
        "meta",
        "guid-only",
        "https://www.cnbc.com/2026/08/20/url.html",
    )


@pytest.mark.parametrize(
    "payload",
    (
        b"not xml",
        b"<rss/>",
        b"<feed><channel/></feed>",
        rss("<item><title>missing fields</title></item>"),
        rss(story("AUTO reports results", published_at="not a date")),
        rss(story("AUTO reports results", sponsored="unknown")),
    ),
)
def test_malformed_xml_or_schema_is_rejected(payload: bytes) -> None:
    with pytest.raises(MalformedCNBCResponse):
        parse_cnbc_rss(payload)


def test_sponsored_and_non_news_items_are_filtered() -> None:
    parsed = parse_cnbc_rss(
        rss(
            story("AUTO reports results", sponsored="true"),
            story(
                "AUTO reports results",
                metadata_type="cnbcvideo",
                url="https://www.cnbc.com/2026/08/20/video.html",
            ),
        )
    )

    assert parsed.stories == ()


class HTTPResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Length": str(len(content))}


class HTTPClient:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def get(self, url: str) -> HTTPResponse:
        self.calls.append(url)
        return self.response


def test_transport_uses_only_official_rss_endpoint() -> None:
    payload = rss(story("AUTO reports Q2 results"))
    client = HTTPClient(HTTPResponse(payload))
    transport = CNBCRSSFeedTransport(client=client)
    feed = CNBCFeed("EARNINGS", 15839135)

    assert transport.fetch_feed(feed) == payload
    assert client.calls == [
        "https://www.cnbc.com/id/15839135/device/rss/rss.html"
    ]


def test_transport_rejects_http_failure_and_oversized_payload() -> None:
    with pytest.raises(CNBCUnavailable):
        CNBCRSSFeedTransport(
            client=HTTPClient(HTTPResponse(b"", status_code=503))
        ).fetch_feed(DEFAULT_CNBC_FEEDS[0])
    with pytest.raises(MalformedCNBCResponse):
        CNBCRSSFeedTransport(
            max_payload_bytes=3,
            client=HTTPClient(HTTPResponse(b"four")),
        ).fetch_feed(DEFAULT_CNBC_FEEDS[0])


def test_four_feeds_are_fetched_once_and_all_symbols_reuse_snapshot() -> None:
    provider, transport = provider_for(
        rss(story("AUTO reports quarterly results")),
        registry=identities(CompanyIdentity("AUTO", ("Auto Corp",))),
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.FALSE
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert transport.calls == list(DEFAULT_CNBC_FEEDS)


def test_expiry_refreshes_entire_snapshot_once() -> None:
    elapsed = [1.0]
    provider, transport = provider_for(
        rss(story("AUTO reports quarterly results")),
        registry=identities(CompanyIdentity("AUTO", ("Auto Corp",))),
        elapsed=elapsed,
        refresh_ttl_seconds=10.0,
    )

    provider.get_evidence("AUTO", NOW)
    elapsed[0] = 10.9
    provider.get_evidence("OTHER", NOW)
    assert len(transport.calls) == 4
    elapsed[0] = 11.1
    provider.get_evidence("AUTO", NOW)
    provider.get_evidence("OTHER", NOW)
    assert len(transport.calls) == 8


def test_advertised_rss_ttl_prevents_more_frequent_refresh() -> None:
    elapsed = [1.0]
    provider, transport = provider_for(
        rss(story("AUTO reports quarterly results"), ttl=1),
        elapsed=elapsed,
        refresh_ttl_seconds=10.0,
    )

    provider.get_evidence("AUTO", NOW)
    elapsed[0] = 59.9
    provider.get_evidence("OTHER", NOW)
    assert len(transport.calls) == 4
    elapsed[0] = 61.1
    provider.get_evidence("OTHER", NOW)
    assert len(transport.calls) == 8


def test_advertised_ttl_beyond_stale_limit_fails_closed() -> None:
    provider, _ = provider_for(
        rss(story("AUTO reports quarterly results"), ttl=2),
        refresh_ttl_seconds=10.0,
        maximum_snapshot_age_seconds=100.0,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


def test_concurrent_expired_lookups_single_flight_one_refresh() -> None:
    class SlowTransport(FeedTransport):
        def fetch_feed(self, feed: CNBCFeed) -> bytes:
            time.sleep(0.01)
            return super().fetch_feed(feed)

    transport = SlowTransport(rss(story("AUTO reports quarterly results")))
    provider, _ = provider_for(
        b"unused",
        registry=identities(CompanyIdentity("AUTO", ("Auto Corp",))),
        transport=transport,
    )
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
    assert len(transport.calls) == 4


def test_outage_cooldown_prevents_symbol_driven_retries() -> None:
    elapsed = [1.0]
    transport = FeedTransport(httpx.ConnectError("offline"))
    provider, _ = provider_for(
        b"unused",
        elapsed=elapsed,
        transport=transport,
        failure_cooldown_seconds=60.0,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE
    elapsed[0] = 30.0
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.UNAVAILABLE
    assert len(transport.calls) == 1
    elapsed[0] = 61.1
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.UNAVAILABLE
    assert len(transport.calls) == 2


def test_expired_snapshot_is_not_used_after_refresh_failure() -> None:
    elapsed = [1.0]
    transport = FeedTransport(
        rss(story("AUTO reports quarterly results")),
        httpx.ConnectError("offline"),
    )
    provider, _ = provider_for(
        b"unused",
        registry=identities(CompanyIdentity("AUTO", ("Auto Corp",))),
        elapsed=elapsed,
        transport=transport,
        refresh_ttl_seconds=10.0,
        maximum_snapshot_age_seconds=20.0,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    elapsed[0] = 11.1
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE


def test_bounded_snapshot_keeps_newest_deterministically() -> None:
    payload = rss(
        story(
            "OLD reports quarterly results",
            story_id="old",
            url="https://www.cnbc.com/2026/08/20/old.html",
            published_at="Thu, 20 Aug 2026 15:30:01 GMT",
        ),
        story(
            "NEW reports quarterly results",
            story_id="new",
            url="https://www.cnbc.com/2026/08/20/new.html",
            published_at="Thu, 20 Aug 2026 16:30:01 GMT",
        ),
    )
    provider, _ = provider_for(payload, max_items=1)

    assert provider.get_evidence("NEW", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OLD", NOW).status is CatalystStatus.FALSE


def test_overlapping_feed_stories_collapse_deterministically(caplog) -> None:
    caplog.set_level("INFO", logger="app.catalysts.cnbc")
    provider, _ = provider_for(rss(story("AUTO reports quarterly results")))

    provider.get_evidence("AUTO", NOW)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=refresh_success feed_count=4 item_count=1" in messages


@pytest.mark.parametrize(
    ("title", "identity", "symbol"),
    (
        ("AUTO reports quarterly results", CompanyIdentity("AUTO"), "AUTO"),
        (
            "Auto Corporation reports quarterly results",
            CompanyIdentity("AUTO", ("Auto Corporation",)),
            "AUTO",
        ),
        (
            "Auto reports quarterly results",
            CompanyIdentity("AUTO", ("Auto",)),
            "AUTO",
        ),
        (
            "AUTO, CORP. reports quarterly results",
            CompanyIdentity("AUTO", ("Auto Corp",)),
            "AUTO",
        ),
        ("BRK.B reports quarterly results", CompanyIdentity("BRK-B"), "brk.b"),
    ),
)
def test_direct_subject_ticker_name_alias_punctuation_and_share_class(
    title: str, identity: CompanyIdentity, symbol: str
) -> None:
    provider, _ = provider_for(rss(story(title)), registry=identities(identity))

    assert provider.get_evidence(symbol, NOW).status is CatalystStatus.TRUE


@pytest.mark.parametrize(
    "title",
    (
        "Qualcomm supplier reports quarterly results",
        "Microsoft reports quarterly results as Apple demand rises",
        "Best Buy reports quarterly results as Apple device sales rise",
        "Semiconductor stocks report strong quarterly results",
    ),
)
def test_indirect_supplier_customer_competitor_and_sector_articles_are_false(
    title: str,
) -> None:
    provider, _ = provider_for(
        rss(story(title)),
        registry=identities(CompanyIdentity("AAPL", ("Apple", "Apple Inc"))),
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.FALSE


def test_description_or_url_only_company_mention_is_false() -> None:
    provider, _ = provider_for(
        rss(
            story(
                "Retailer reports quarterly results",
                description="Auto Corp reported earnings",
                url="https://www.cnbc.com/2026/08/20/auto-auto-earnings.html",
            )
        ),
        registry=identities(CompanyIdentity("AUTO", ("Auto Corp",))),
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


def test_unknown_identity_without_ticker_is_false() -> None:
    provider, _ = provider_for(rss(story("Auto Corp reports quarterly results")))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


def test_ambiguous_common_word_ticker_requires_identity() -> None:
    provider, _ = provider_for(rss(story("IT reports quarterly results")))

    assert provider.get_evidence("IT", NOW).status is CatalystStatus.FALSE


def test_ambiguous_alias_is_removed_from_every_identity() -> None:
    registry = identities(
        CompanyIdentity("AAA", ("Universal",)),
        CompanyIdentity("BBB", ("Universal",)),
    )
    provider, _ = provider_for(
        rss(story("Universal reports quarterly results")), registry=registry
    )

    assert provider.get_evidence("AAA", NOW).status is CatalystStatus.FALSE
    assert provider.get_evidence("BBB", NOW).status is CatalystStatus.FALSE


def test_two_explicitly_named_companies_are_handled_deterministically() -> None:
    registry = identities(
        CompanyIdentity("AAPL", ("Apple",)),
        CompanyIdentity("QCOM", ("Qualcomm",)),
    )
    provider, transport = provider_for(
        rss(story("Apple and Qualcomm announce partnership")), registry=registry
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("QCOM", NOW).status is CatalystStatus.TRUE
    assert len(transport.calls) == 4


@pytest.mark.parametrize(
    ("headline", "expected"),
    (
        ("AUTO reports quarterly earnings results", CatalystType.EARNINGS),
        ("AUTO raises full-year guidance", CatalystType.GUIDANCE),
        ("FDA approves AUTO therapy", CatalystType.FDA),
        ("FDA rejects AUTO therapy", CatalystType.FDA),
        ("AUTO clinical trial meets primary endpoint", CatalystType.CLINICAL_TRIAL),
        ("AUTO agrees to acquire Parts Inc", CatalystType.ACQUISITION),
        ("AUTO wins government contract award", CatalystType.CONTRACT),
        ("AUTO announces partnership with Parts Inc", CatalystType.PARTNERSHIP),
        ("AUTO files Form 8-K with SEC", CatalystType.SEC_FILING),
        ("AUTO declares quarterly dividend", CatalystType.OTHER),
    ),
)
def test_supported_cnbc_classification(
    headline: str, expected: CatalystType
) -> None:
    assert classify_cnbc_headline(headline) is expected


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
def test_editorial_price_recap_preview_and_speculation_are_rejected(
    headline: str,
) -> None:
    assert classify_cnbc_headline(headline) is None


@dataclass
class StaticProvider:
    name: str
    item: CatalystEvidence

    def get_evidence(
        self, symbol: str, as_of: datetime | None = None
    ) -> CatalystEvidence:
        return self.item


def static_evidence(
    source: str,
    catalyst_type: CatalystType,
    headline: str,
    *,
    canonical_event_id: str | None = None,
) -> CatalystEvidence:
    return CatalystEvidence(
        symbol="AUTO",
        catalyst_type=catalyst_type,
        status=CatalystStatus.TRUE,
        headline=headline,
        source=source,
        published_at=NOW - timedelta(minutes=30),
        provider_event_id=source + "-1",
        canonical_event_id=canonical_event_id,
    )


def test_cnbc_yahoo_corroboration_retains_both_sources() -> None:
    headline = "AUTO reports quarterly earnings results"
    provider, _ = provider_for(rss(story(headline)))
    canonical = canonical_headline_event_id(
        "AUTO", CatalystType.EARNINGS, headline, NOW - timedelta(minutes=29, seconds=59)
    )
    yahoo = StaticProvider(
        "YAHOO_FINANCE",
        static_evidence(
            "YAHOO_FINANCE",
            CatalystType.EARNINGS,
            headline,
            canonical_event_id=canonical,
        ),
    )

    result = CatalystAggregator((provider, yahoo)).aggregate_result("AUTO", NOW)

    assert len(result.events) == 1
    assert result.events[0].sources == ("CNBC", "YAHOO_FINANCE")


def test_cnbc_sec_accession_corroborates_edgar() -> None:
    headline = "AUTO files Form 8-K with SEC 0000123456-26-000007"
    provider, _ = provider_for(rss(story(headline)))
    sec = StaticProvider(
        "SEC_EDGAR",
        static_evidence(
            "SEC_EDGAR",
            CatalystType.SEC_FILING,
            "SEC 8-K filing",
            canonical_event_id="sec-filing:0000123456-26-000007",
        ),
    )

    result = CatalystAggregator((provider, sec)).aggregate_result("AUTO", NOW)

    assert len(result.events) == 1
    assert result.events[0].sources == ("CNBC", "SEC_EDGAR")


def test_cnbc_and_webull_do_not_force_insufficient_earnings_identity() -> None:
    provider, _ = provider_for(rss(story("AUTO reports quarterly earnings results")))
    webull = StaticProvider(
        "WEBULL_EARNINGS_SEC",
        static_evidence(
            "WEBULL_EARNINGS",
            CatalystType.EARNINGS,
            "Q2",
        ),
    )

    result = CatalystAggregator((provider, webull)).aggregate_result("AUTO", NOW)

    assert len(result.events) == 2
    assert {item.source for item in result.evidence} == {"CNBC", "WEBULL_EARNINGS"}


def test_provider_permutations_and_failure_isolation_are_deterministic() -> None:
    provider, _ = provider_for(rss(story("AUTO reports quarterly earnings results")))
    sec_item = static_evidence("SEC_EDGAR", CatalystType.SEC_FILING, "SEC 8-K filing")
    providers = (provider, StaticProvider("SEC_EDGAR", sec_item))

    results = tuple(
        CatalystAggregator(ordering).aggregate_result("AUTO", NOW)
        for ordering in permutations(providers)
    )
    assert results[0] == results[1]

    failed, _ = provider_for(httpx.ConnectError("offline"))
    isolated = CatalystAggregator(
        (failed, StaticProvider("SEC_EDGAR", sec_item))
    ).aggregate_result("AUTO", NOW)
    assert isolated.selected == sec_item
    assert any(item.status is CatalystStatus.UNAVAILABLE for item in isolated.evidence)


def test_configuration_is_opt_in_and_composes_expected_policy() -> None:
    disabled = load_configuration({})
    enabled = load_configuration(
        {
            "CNBC_NEWS_ENABLED": "true",
            "CNBC_NEWS_FRESHNESS_MINUTES": "720",
            "CNBC_NEWS_TIMEOUT_SECONDS": "2.5",
            "CNBC_NEWS_REFRESH_TTL_SECONDS": "1800",
            "CNBC_NEWS_FAILURE_COOLDOWN_SECONDS": "120",
            "CNBC_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS": "3600",
            "CNBC_NEWS_MAX_ITEMS": "128",
            "CNBC_NEWS_MAX_PAYLOAD_BYTES": "500000",
        }
    )

    assert disabled.cnbc_news is None
    assert enabled.cnbc_news is not None
    assert enabled.cnbc_news.freshness_minutes == 720
    assert enabled.cnbc_news.timeout_seconds == 2.5
    assert enabled.cnbc_news.refresh_ttl_seconds == 1800.0
    assert enabled.cnbc_news.failure_cooldown_seconds == 120.0
    assert enabled.cnbc_news.maximum_snapshot_age_seconds == 3600.0
    assert enabled.cnbc_news.max_items == 128
    assert enabled.cnbc_news.max_payload_bytes == 500000
    providers = build_catalyst_providers(SimpleNamespace(), enabled)
    assert tuple(item.name for item in providers) == (
        "WEBULL_EARNINGS_SEC",
        "CNBC",
    )


def test_observability_omits_payload_and_exception_text(caplog) -> None:
    caplog.set_level("DEBUG", logger="app.catalysts.cnbc")
    secret = "private payload token"
    provider, _ = provider_for(httpx.ConnectError(secret))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=provider_state enabled=true" in messages
    assert "event=cache_miss scope=feed_snapshot" in messages
    assert "event=refresh_failure reason=network" in messages
    assert "event=provider_unavailable symbol=AUTO" in messages
    assert secret not in messages
