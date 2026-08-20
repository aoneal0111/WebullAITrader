from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import permutations
from types import SimpleNamespace

import httpx
import pytest

from app.catalysts import (
    CatalystAggregator,
    CatalystEvidence,
    CatalystStatus,
    CatalystType,
    SECEdgarCatalystProvider,
    SECEdgarPolicy,
    WebullCatalystProvider,
    YahooFinanceCatalystProvider,
    YahooFinanceNewsPolicy,
    YahooFinanceSearchTransport,
    YahooFinanceUnavailable,
    build_catalyst_providers,
    classify_yahoo_headline,
)
from app.configuration import load_configuration


NOW = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)


class Transport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def fetch_news(self, symbol: str) -> object:
        self.calls.append(symbol)
        result = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def news_item(
    title: str,
    *,
    published_at: datetime = NOW - timedelta(minutes=10),
    tickers: tuple[str, ...] = ("AUTO",),
    event_id: str = "story-123",
    link: str = "https://finance.yahoo.com/news/story-123.html",
) -> dict[str, object]:
    return {
        "uuid": event_id,
        "title": title,
        "publisher": "Example News",
        "link": link,
        "providerPublishTime": int(published_at.timestamp()),
        "relatedTickers": list(tickers),
        "type": "STORY",
    }


def quote_identity(
    symbol: str = "AUTO",
    *,
    shortname: object = "Auto Corp",
    longname: object = "Auto Corporation",
) -> dict[str, object]:
    return {"symbol": symbol, "shortname": shortname, "longname": longname}


def payload(
    *items: dict[str, object],
    quotes: object | None = None,
) -> dict[str, object]:
    return {
        "quotes": [quote_identity()] if quotes is None else quotes,
        "news": list(items),
    }


def provider_for(
    response: object,
    *,
    elapsed: list[float] | None = None,
    **policy_values: object,
) -> tuple[YahooFinanceCatalystProvider, Transport]:
    transport = Transport(response)
    clock = elapsed or [1.0]
    provider = YahooFinanceCatalystProvider(
        YahooFinanceNewsPolicy(**policy_values),
        transport=transport,
        monotonic=lambda: clock[0],
    )
    return provider, transport


def test_valid_fresh_headline_preserves_evidence_and_scanner_tuple() -> None:
    provider, transport = provider_for(
        payload(news_item("Auto Corp reports second-quarter results"))
    )

    result = provider.get_evidence(" auto ", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.EARNINGS,
        "Auto Corp reports second-quarter results",
        CatalystStatus.TRUE,
    )
    assert result.source == "YAHOO_FINANCE"
    assert result.published_at == NOW - timedelta(minutes=10)
    assert result.source_url == "https://finance.yahoo.com/news/story-123.html"
    assert result.provider_event_id == "story-123"
    assert result.canonical_event_id is not None
    assert transport.calls == ["AUTO"]


def test_stale_headline_returns_false() -> None:
    provider, _ = provider_for(
        payload(
            news_item(
                "Auto Corp reports second-quarter results",
                published_at=NOW - timedelta(hours=25),
            )
        )
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


@pytest.mark.parametrize(
    ("headline", "expected"),
    (
        ("Auto Corp reports quarterly earnings results", CatalystType.EARNINGS),
        ("Auto Corp reports Q2 earnings", CatalystType.EARNINGS),
        ("Auto Corp raises full-year guidance", CatalystType.GUIDANCE),
        ("FDA approves Auto Corp therapy", CatalystType.FDA),
        ("Auto Corp clinical trial meets primary endpoint", CatalystType.CLINICAL_TRIAL),
        ("Auto Corp to acquire Parts Inc in announced merger", CatalystType.ACQUISITION),
        ("Auto Corp wins government contract award", CatalystType.CONTRACT),
        ("Auto Corp announces partnership with Parts Inc", CatalystType.PARTNERSHIP),
        ("Auto Corp files Form 8-K with SEC", CatalystType.SEC_FILING),
        ("Auto Corp declares quarterly dividend", CatalystType.OTHER),
    ),
)
def test_conservative_supported_classification(
    headline: str, expected: CatalystType
) -> None:
    assert classify_yahoo_headline(headline) is expected


@pytest.mark.parametrize(
    "headline",
    (
        "Market recap: stocks finish mixed as Auto Corp rises",
        "Why Auto Corp stock jumped today",
        "Technical analysis for Auto Corp shares",
        "Top 10 best stocks to buy now including Auto Corp",
        "Should you buy Auto Corp before earnings?",
        "Opinion: what investors should know about Auto Corp",
        "Auto Corp earnings preview",
        "Analysts debate FDA approval odds for Auto Corp",
        "Rumor: Auto Corp acquisition may be coming",
        "Auto Corp discusses its business",
    ),
)
def test_generic_market_summary_opinion_listicle_and_vague_items_are_rejected(
    headline: str,
) -> None:
    provider, _ = provider_for(payload(news_item(headline)))

    result = provider.get_evidence("AUTO", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.NONE,
        None,
        CatalystStatus.FALSE,
    )


def test_unrelated_symbol_is_rejected_even_with_strong_headline() -> None:
    provider, _ = provider_for(
        payload(
            news_item(
                "Other Corp reports quarterly results",
                tickers=("OTHER",),
            )
        )
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


def test_live_smoke_regression_related_aapl_is_not_qualcomm_subject() -> None:
    provider, _ = provider_for(
        payload(
            news_item(
                "Qualcomm Q3 Results Put Auto Growth and Handset Weakness in Focus",
                tickers=("QCOM", "AAPL"),
            ),
            quotes=[
                quote_identity("AAPL", shortname="Apple Inc.", longname="Apple Inc.")
            ],
        )
    )

    result = provider.get_evidence("AAPL", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.NONE,
        None,
        CatalystStatus.FALSE,
    )


@pytest.mark.parametrize(
    "headline",
    (
        "Qualcomm supplier reports quarterly results as handset demand softens",
        "Microsoft reports quarterly results as cloud demand rises",
        "Best Buy reports quarterly results as device sales rise",
    ),
    ids=("supplier", "competitor", "customer"),
)
def test_related_but_indirect_company_article_is_rejected(headline: str) -> None:
    provider, _ = provider_for(
        payload(
            news_item(headline, tickers=("AAPL", "OTHER")),
            quotes=[
                quote_identity("AAPL", shortname="Apple Inc.", longname="Apple Inc.")
            ],
        )
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.FALSE


@pytest.mark.parametrize(
    "headline",
    (
        "TSMC Raises Guidance as AI Demand Surges",
        "5 Semiconductor Stocks to Watch After Nvidia Earnings",
    ),
)
def test_related_unnamed_company_is_rejected(headline: str) -> None:
    provider, _ = provider_for(
        payload(
            news_item(headline, tickers=("AAPL", "TSM", "NVDA")),
            quotes=[
                quote_identity("AAPL", shortname="Apple Inc.", longname="Apple Inc.")
            ],
        )
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.FALSE


def test_explicit_ticker_is_eligible_without_name_metadata() -> None:
    provider, _ = provider_for(
        payload(
            news_item("AAPL Reports Q3 Results", tickers=("AAPL",)),
            quotes=[],
        )
    )

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.TRUE


@pytest.mark.parametrize(
    ("headline", "shortname", "longname"),
    (
        ("Apple Reports Record Quarterly Earnings", "Apple", "Apple Inc."),
        (
            "International Business Machines Corporation reports quarterly results",
            "IBM",
            "International Business Machines Corporation",
        ),
        ("APPLE, INC. Raises Full-Year Guidance", "Apple Inc.", "Apple Inc."),
    ),
    ids=("short-name", "long-name", "punctuation-and-case"),
)
def test_explicit_company_identity_alias_is_eligible(
    headline: str, shortname: str, longname: str
) -> None:
    symbol = "AAPL" if "Apple" in longname else "IBM"
    provider, _ = provider_for(
        payload(
            news_item(headline, tickers=(symbol,)),
            quotes=[quote_identity(symbol, shortname=shortname, longname=longname)],
        )
    )

    assert provider.get_evidence(symbol, NOW).status is CatalystStatus.TRUE


def test_two_explicitly_named_companies_can_each_qualify() -> None:
    story = news_item(
        "Apple and Qualcomm Announce Partnership",
        tickers=("AAPL", "QCOM"),
    )
    apple, _ = provider_for(
        payload(
            story,
            quotes=[quote_identity("AAPL", shortname="Apple", longname="Apple Inc.")],
        )
    )
    qualcomm, _ = provider_for(
        payload(
            story,
            quotes=[
                quote_identity(
                    "QCOM", shortname="Qualcomm", longname="Qualcomm Incorporated"
                )
            ],
        )
    )

    assert apple.get_evidence("AAPL", NOW).status is CatalystStatus.TRUE
    assert qualcomm.get_evidence("QCOM", NOW).status is CatalystStatus.TRUE


def test_share_class_tickers_normalize_for_direct_association() -> None:
    provider, _ = provider_for(
        payload(
            news_item(
                "BRK.B reports quarterly results",
                tickers=("BRK-B",),
            ),
            quotes=[
                quote_identity(
                    "BRK-B",
                    shortname="Berkshire Hathaway Inc.",
                    longname="Berkshire Hathaway Inc.",
                )
            ],
        )
    )

    result = provider.get_evidence("brk.b", NOW)

    assert result.status is CatalystStatus.TRUE
    assert result.symbol == "BRK-B"


def test_sec_accession_uses_cross_source_canonical_identity_when_available() -> None:
    provider, _ = provider_for(
        payload(
            news_item(
                "Auto Corp files Form 8-K with SEC",
                link=(
                    "https://www.sec.gov/Archives/edgar/data/123456/"
                    "000012345626000007/auto-20260820.htm"
                ),
            )
        )
    )

    result = provider.get_evidence("AUTO", NOW)

    assert result.catalyst_type is CatalystType.SEC_FILING
    assert result.canonical_event_id == "sec-filing:0000123456-26-000007"


@pytest.mark.parametrize(
    "malformed",
    (
        {},
        {"news": {}},
        {"news": [None]},
        {"news": [{"title": "missing fields"}]},
        {"news": [news_item("Auto Corp reports quarterly results") | {"relatedTickers": "AUTO"}]},
    ),
)
def test_malformed_response_returns_unknown(malformed: object) -> None:
    provider, _ = provider_for(malformed)

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


@pytest.mark.parametrize("status", (403, 429, 500, 503))
def test_http_provider_failures_return_unavailable(status: int) -> None:
    provider, _ = provider_for(
        YahooFinanceUnavailable(f"HTTP {status}", status_code=status)
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE


@pytest.mark.parametrize(
    "error",
    (
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("offline"),
    ),
)
def test_timeout_and_network_failures_return_unavailable(error: Exception) -> None:
    provider, _ = provider_for(error)

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE


class HTTPResponse:
    def __init__(self, value: object, status_code: int = 200) -> None:
        self.value = value
        self.status_code = status_code

    def json(self) -> object:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class HTTPClient:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> HTTPResponse:
        self.calls.append((url, kwargs))
        return self.response


def test_transport_uses_structured_json_endpoint_and_bounded_query() -> None:
    expected = payload(news_item("Auto Corp reports quarterly results"))
    client = HTTPClient(HTTPResponse(expected))
    transport = YahooFinanceSearchTransport(news_count=7, client=client)

    assert transport.fetch_news("AUTO") == expected
    assert client.calls == [
        (
            "https://query1.finance.yahoo.com/v1/finance/search",
            {
                "params": {
                    "q": "AUTO",
                    "quotesCount": 5,
                    "newsCount": 7,
                    "enableFuzzyQuery": "false",
                    "region": "US",
                    "lang": "en-US",
                }
            },
        )
    ]


@pytest.mark.parametrize("status", (403, 429, 500, 503))
def test_transport_maps_non_success_http_status_to_unavailable(status: int) -> None:
    transport = YahooFinanceSearchTransport(
        client=HTTPClient(HTTPResponse({}, status_code=status))
    )

    with pytest.raises(YahooFinanceUnavailable) as caught:
        transport.fetch_news("AUTO")

    assert caught.value.status_code == status


def test_cache_reuse_across_scanner_cycles_and_expiry() -> None:
    elapsed = [1.0]
    provider, transport = provider_for(
        payload(
            news_item("Apple reports quarterly results", tickers=("AAPL",)),
            quotes=[quote_identity("AAPL", shortname="Apple", longname="Apple Inc.")],
        ),
        elapsed=elapsed,
        cache_ttl_seconds=300.0,
    )

    for cycle in (1.0, 60.0, 299.0, 300.9):
        elapsed[0] = cycle
        assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.TRUE
    assert transport.calls == ["AAPL"]

    elapsed[0] = 301.1
    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.TRUE
    assert transport.calls == ["AAPL", "AAPL"]


@pytest.mark.parametrize(
    "quotes",
    (
        None,
        {},
        [None],
        [{"symbol": "AAPL", "shortname": 42, "longname": []}],
        [quote_identity("WRONG", shortname="Apple", longname="Apple Inc.")],
    ),
)
def test_missing_or_malformed_identity_metadata_fails_conservatively(
    quotes: object,
) -> None:
    response = {
        "news": [
            news_item(
                "Apple Reports Record Quarterly Earnings",
                tickers=("AAPL",),
            )
        ]
    }
    if quotes is not None:
        response["quotes"] = quotes
    provider, _ = provider_for(response)

    assert provider.get_evidence("AAPL", NOW).status is CatalystStatus.FALSE


def test_outage_cooldown_prevents_requests_until_expiry() -> None:
    elapsed = [1.0]
    success = payload(news_item("Auto Corp reports quarterly results"))
    transport = Transport(httpx.ConnectError("offline"), success)
    provider = YahooFinanceCatalystProvider(
        YahooFinanceNewsPolicy(failure_cooldown_seconds=60.0),
        transport=transport,
        monotonic=lambda: elapsed[0],
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE
    elapsed[0] = 30.0
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.UNAVAILABLE
    assert transport.calls == ["AUTO"]

    elapsed[0] = 61.1
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert transport.calls == ["AUTO", "AUTO"]


def test_symbol_cache_is_bounded_and_evicts_least_recent_entry() -> None:
    response = payload(news_item("Auto Corp reports quarterly results"))
    transport = Transport(response)
    provider = YahooFinanceCatalystProvider(
        YahooFinanceNewsPolicy(max_cache_entries=1),
        transport=transport,
        monotonic=lambda: 1.0,
    )

    provider.get_evidence("AUTO", NOW)
    provider.get_evidence("OTHER", NOW)
    provider.get_evidence("AUTO", NOW)

    assert transport.calls == ["AUTO", "OTHER", "AUTO"]


@dataclass
class StubProvider:
    name: str
    evidence: CatalystEvidence

    def get_evidence(self, symbol: str, as_of: datetime | None = None) -> CatalystEvidence:
        return self.evidence


def evidence(
    source: str,
    status: CatalystStatus,
    *,
    catalyst_type: CatalystType = CatalystType.NONE,
    canonical_event_id: str | None = None,
) -> CatalystEvidence:
    return CatalystEvidence(
        symbol="AUTO",
        catalyst_type=catalyst_type,
        status=status,
        headline=("Auto Corp reports quarterly results" if status is CatalystStatus.TRUE else None),
        source=source,
        published_at=(NOW - timedelta(minutes=10) if status is CatalystStatus.TRUE else None),
        provider_event_id=(source + "-1" if status is CatalystStatus.TRUE else None),
        canonical_event_id=canonical_event_id,
    )


class Fundamentals:
    def get_earnings_calendar(self, symbol: str, category: str) -> HTTPResponse:
        return HTTPResponse([])

    def get_sec_filings(self, symbol: str, category: str) -> HTTPResponse:
        return HTTPResponse([])


class SECClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: object) -> HTTPResponse:
        self.calls.append(url)
        if url.endswith("company_tickers.json"):
            return HTTPResponse(
                {"0": {"ticker": "AUTO", "cik_str": 123456, "title": "Auto Corp"}}
            )
        return HTTPResponse(
            {
                "cik": "0000123456",
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "filingDate": ["2026-08-20"],
                        "acceptanceDateTime": ["2026-08-20T14:00:00Z"],
                        "accessionNumber": ["0000123456-26-000007"],
                        "primaryDocument": ["auto-20260820.htm"],
                    }
                },
            }
        )


def real_sec_and_webull_providers() -> tuple[object, object]:
    sec = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent="WebullAITrader tests contact@example.invalid"),
        client=SECClient(),
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )
    webull = WebullCatalystProvider(SimpleNamespace(fundamentals=Fundamentals()))
    return sec, webull


def test_yahoo_sec_webull_provider_permutations_are_order_independent() -> None:
    yahoo_provider, _ = provider_for(
        payload(news_item("Auto Corp reports quarterly results"))
    )
    sec, webull = real_sec_and_webull_providers()

    results = tuple(
        CatalystAggregator(ordering).aggregate_result("AUTO", NOW)
        for ordering in permutations((yahoo_provider, sec, webull))
    )

    assert all(result == results[0] for result in results)
    assert results[0].selected.source == "YAHOO_FINANCE"


def test_duplicate_event_retains_yahoo_as_corroborating_evidence() -> None:
    yahoo_provider, _ = provider_for(
        payload(news_item("Auto Corp reports quarterly results"))
    )
    yahoo = yahoo_provider.get_evidence("AUTO", NOW)
    corroboration = evidence(
        "PRESS_RELEASE",
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        canonical_event_id=yahoo.canonical_event_id,
    )

    result = CatalystAggregator(
        (yahoo_provider, StubProvider("PRESS_RELEASE", corroboration))
    ).aggregate_result("AUTO", NOW)

    assert len(result.events) == 1
    assert result.events[0].sources == ("PRESS_RELEASE", "YAHOO_FINANCE")
    assert len(result.events[0].evidence) == 2


def test_yahoo_outage_does_not_erase_sec_or_webull_success() -> None:
    yahoo_provider, _ = provider_for(httpx.ConnectError("offline"))
    sec, webull = real_sec_and_webull_providers()

    result = CatalystAggregator((yahoo_provider, sec, webull)).aggregate_result(
        "AUTO", NOW
    )

    assert result.selected.source == "SEC_EDGAR"
    assert any(
        item.source == "YAHOO_FINANCE" and item.status is CatalystStatus.UNAVAILABLE
        for item in result.evidence
    )


def test_configuration_defaults_disabled_and_parses_explicit_yahoo_policy() -> None:
    disabled = load_configuration({})
    enabled = load_configuration(
        {
            "YAHOO_FINANCE_NEWS_ENABLED": "true",
            "YAHOO_FINANCE_NEWS_FRESHNESS_MINUTES": "720",
            "YAHOO_FINANCE_TIMEOUT_SECONDS": "2.5",
            "YAHOO_FINANCE_NEWS_CACHE_TTL_SECONDS": "180",
        }
    )

    assert disabled.yahoo_finance_news is None
    assert enabled.yahoo_finance_news is not None
    assert enabled.yahoo_finance_news.freshness_minutes == 720
    assert enabled.yahoo_finance_news.timeout_seconds == 2.5
    assert enabled.yahoo_finance_news.cache_ttl_seconds == 180.0


def test_disabled_composition_preserves_webull_only_path() -> None:
    configuration = load_configuration({})
    providers = build_catalyst_providers(SimpleNamespace(), configuration)

    assert tuple(item.name for item in providers) == ("WEBULL_EARNINGS_SEC",)


def test_observability_omits_payload_and_exception_message(caplog) -> None:
    caplog.set_level("DEBUG", logger="app.catalysts.yahoo_finance")
    secret = "private response payload and token"
    provider, _ = provider_for(httpx.ConnectError(secret))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=provider_state enabled=true" in messages
    assert "event=cache_miss symbol=AUTO" in messages
    assert "event=request_failure symbol=AUTO reason=network" in messages
    assert "event=provider_unavailable symbol=AUTO" in messages
    assert secret not in messages
