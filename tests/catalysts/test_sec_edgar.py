from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import permutations
from types import SimpleNamespace

import httpx
import pytest

from app.catalysts import (
    CatalystAggregator,
    CatalystEvidence,
    SECEdgarCatalystProvider,
    SECEdgarPolicy,
    WebullCatalystProvider,
    log_sec_edgar_provider_state,
)
from app.configuration import load_configuration
from app.momentum_scanner.models import CatalystStatus, CatalystType


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000123456.json"


class Response:
    def __init__(self, value: object, status_code: int = 200) -> None:
        self.value = value
        self.status_code = status_code

    def json(self) -> object:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Client:
    def __init__(self, responses: dict[str, Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> Response:
        self.calls.append((url, kwargs))
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def tickers(*, ticker: str = "AUTO") -> dict[str, object]:
    return {"0": {"ticker": ticker, "cik_str": 123456, "title": "Auto Corp"}}


def submissions(
    *,
    form: str = "8-K",
    filing_date: str = "2026-07-30",
    acceptance: str = "2026-07-30T09:30:00.000Z",
    accession: str = "0000123456-26-000007",
    document: str = "auto-20260730.htm",
) -> dict[str, object]:
    return {
        "cik": "0000123456",
        "filings": {
            "recent": {
                "form": [form],
                "filingDate": [filing_date],
                "acceptanceDateTime": [acceptance],
                "accessionNumber": [accession],
                "primaryDocument": [document],
            }
        },
    }


def provider_for(
    submissions_response: Response | Exception,
    *,
    ticker_response: Response | Exception | None = None,
    freshness_days: int = 3,
) -> tuple[SECEdgarCatalystProvider, Client]:
    client = Client(
        {
            TICKERS_URL: ticker_response or Response(tickers()),
            SUBMISSIONS_URL: submissions_response,
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(
            user_agent="WebullAITrader tests contact@example.invalid",
            freshness_days=freshness_days,
        ),
        client=client,
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )
    return provider, client


def test_8k_evidence_preserves_sec_fields_and_scanner_tuple() -> None:
    provider, client = provider_for(Response(submissions()))

    result = provider.get_evidence(" auto ", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.SEC_FILING,
        "SEC 8-K filing",
        CatalystStatus.TRUE,
    )
    assert result.source == "SEC_EDGAR"
    assert result.published_at == datetime(2026, 7, 30, 9, 30, tzinfo=UTC)
    assert result.provider_event_id == "0000123456-26-000007"
    assert result.canonical_event_id == "sec-filing:0000123456-26-000007"
    assert result.source_url == (
        "https://www.sec.gov/Archives/edgar/data/123456/"
        "000012345626000007/auto-20260730.htm"
    )
    assert [call[0] for call in client.calls] == [TICKERS_URL, SUBMISSIONS_URL]
    assert all(
        call[1]["headers"]["User-Agent"]
        == "WebullAITrader tests contact@example.invalid"
        for call in client.calls
    )


def test_provider_observability_is_structured_and_redacted(caplog) -> None:
    caplog.set_level("DEBUG", logger="app.catalysts.sec_edgar")
    user_agent = "private operator contact@example.invalid"
    client = Client(
        {
            TICKERS_URL: Response(tickers()),
            SUBMISSIONS_URL: Response(submissions()),
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent=user_agent),
        client=client,
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE

    messages = tuple(record.getMessage() for record in caplog.records)
    combined = "\n".join(messages)
    assert "event=provider_state enabled=true" in combined
    assert "event=request_success endpoint=company_tickers status=200" in combined
    assert "event=request_success endpoint=company_submissions status=200" in combined
    assert "event=cache_miss cache=ticker_mapping" in combined
    assert "event=cache_hit cache=ticker_mapping" in combined
    assert "event=cache_miss cache=recent_submissions" in combined
    assert "event=cache_hit cache=recent_submissions" in combined
    assert "event=filing_evidence_found symbol=AUTO form=8-K" in combined
    assert user_agent not in combined
    assert "Auto Corp" not in combined


def test_disabled_provider_observability_contains_no_configuration(caplog) -> None:
    caplog.set_level("INFO", logger="app.catalysts.sec_edgar")

    log_sec_edgar_provider_state(enabled=False)

    assert caplog.records[-1].getMessage() == (
        "sec_edgar_event event=provider_state enabled=false"
    )


@pytest.mark.parametrize(
    "form",
    (
        "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "S-1", "S-1/A",
        "S-3", "S-3/A", "424B2", "424B5", "SC 13D", "SC 13D/A",
        "SC 13G", "SC 13G/A",
    ),
)
def test_relevant_forms_and_amendments_are_classified_conservatively(form: str) -> None:
    provider, _ = provider_for(Response(submissions(form=form)))

    result = provider.get_evidence("AUTO", NOW)

    assert result.status is CatalystStatus.TRUE
    assert result.catalyst_type is CatalystType.SEC_FILING
    assert result.headline == f"SEC {form} filing"


def test_routine_insider_filing_is_not_promoted_to_catalyst() -> None:
    provider, _ = provider_for(Response(submissions(form="4")))

    result = provider.get_evidence("AUTO", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.NONE,
        None,
        CatalystStatus.FALSE,
    )


@pytest.mark.parametrize("filing_date", ("2026-07-26", "2026-08-01"))
def test_stale_or_future_filing_is_rejected(filing_date: str) -> None:
    provider, _ = provider_for(Response(submissions(filing_date=filing_date)))

    result = provider.get_evidence("AUTO", NOW)

    assert result.status is CatalystStatus.FALSE
    assert result.catalyst_type is CatalystType.NONE


def test_later_same_day_acceptance_is_not_visible_at_as_of_time() -> None:
    provider, _ = provider_for(
        Response(submissions(acceptance="2026-07-30T16:00:00Z"))
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


def test_naive_sec_acceptance_timestamp_is_interpreted_as_eastern_and_stored_utc() -> None:
    provider, _ = provider_for(
        Response(submissions(acceptance="2026-07-30T09:30:00"))
    )

    result = provider.get_evidence("AUTO", NOW)

    assert result.published_at == datetime(2026, 7, 30, 13, 30, tzinfo=UTC)


def test_empty_recent_filings_returns_false() -> None:
    payload = submissions()
    recent = payload["filings"]["recent"]  # type: ignore[index]
    for key in tuple(recent):  # type: ignore[arg-type]
        recent[key] = []  # type: ignore[index]
    provider, _ = provider_for(Response(payload))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.FALSE


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"filings": {}},
        {"filings": {"recent": {"form": ["8-K"]}}},
        {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": [],
                    "filingDate": ["2026-07-30"],
                }
            }
        },
    ),
)
def test_malformed_submissions_schema_returns_unknown(payload: object) -> None:
    provider, _ = provider_for(Response(payload))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


@pytest.mark.parametrize("status", (403, 429, 500, 503))
def test_sec_http_outages_return_unavailable(status: int) -> None:
    provider, _ = provider_for(Response({}, status_code=status))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE


def test_sec_outage_cooldown_prevents_repeated_http_requests() -> None:
    provider, client = provider_for(Response({}, status_code=429))

    results = tuple(provider.get_evidence("AUTO", NOW) for _ in range(5))

    assert all(result.status is CatalystStatus.UNAVAILABLE for result in results)
    assert [call[0] for call in client.calls] == [TICKERS_URL, SUBMISSIONS_URL]


def test_unavailable_observability_omits_exception_message(caplog) -> None:
    caplog.set_level("DEBUG", logger="app.catalysts.sec_edgar")
    private_message = "private-contact@example.invalid failed"
    provider, _ = provider_for(httpx.ConnectError(private_message))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert (
        "event=request_failure endpoint=company_submissions "
        "reason=network status=none error_type=ConnectError"
    ) in combined
    assert (
        "event=provider_unavailable reason=network "
        "endpoint=company_submissions status=none"
    ) in combined
    assert private_message not in combined


@pytest.mark.parametrize(
    "error",
    (
        httpx.ConnectError("offline"),
        httpx.ReadTimeout("slow"),
    ),
)
def test_sec_network_outages_return_unavailable(error: Exception) -> None:
    provider, _ = provider_for(error)

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNAVAILABLE


def test_malformed_json_returns_unknown() -> None:
    provider, _ = provider_for(Response(ValueError("bad json")))

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


def test_ticker_and_submission_caches_prevent_repeated_requests() -> None:
    provider, client = provider_for(Response(submissions()))

    results = tuple(provider.get_evidence("AUTO", NOW) for _ in range(5))

    assert all(result == results[0] for result in results)
    assert [call[0] for call in client.calls] == [TICKERS_URL, SUBMISSIONS_URL]


def test_scanner_cycles_reuse_submissions_until_cache_expiry() -> None:
    elapsed = [1.0]
    client = Client(
        {
            TICKERS_URL: Response(tickers()),
            SUBMISSIONS_URL: Response(submissions()),
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(
            user_agent="WebullAITrader tests contact@example.invalid",
            submissions_cache_seconds=900.0,
        ),
        client=client,
        monotonic=lambda: elapsed[0],
        sleep=lambda _: None,
    )

    for cycle_time in (1.0, 61.0, 301.0, 899.0):
        elapsed[0] = cycle_time
        assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE

    assert [call[0] for call in client.calls] == [TICKERS_URL, SUBMISSIONS_URL]

    elapsed[0] = 902.0
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert [call[0] for call in client.calls] == [
        TICKERS_URL,
        SUBMISSIONS_URL,
        SUBMISSIONS_URL,
    ]


def test_ticker_mapping_refreshes_only_after_its_ttl() -> None:
    elapsed = [1.0]
    client = Client(
        {
            TICKERS_URL: Response(tickers()),
            SUBMISSIONS_URL: Response(submissions()),
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent="configured agent"),
        client=client,
        monotonic=lambda: elapsed[0],
        sleep=lambda _: None,
    )

    assert provider.get_evidence("MISSING", NOW).status is CatalystStatus.FALSE
    elapsed[0] = 86_400.0
    assert provider.get_evidence("MISSING", NOW).status is CatalystStatus.FALSE
    assert [call[0] for call in client.calls] == [TICKERS_URL]

    elapsed[0] = 86_402.0
    assert provider.get_evidence("MISSING", NOW).status is CatalystStatus.FALSE
    assert [call[0] for call in client.calls] == [TICKERS_URL, TICKERS_URL]


def test_submissions_cache_evicts_oldest_entry_at_configured_bound() -> None:
    second_submissions_url = (
        "https://data.sec.gov/submissions/CIK0000654321.json"
    )
    client = Client(
        {
            TICKERS_URL: Response(
                {
                    "0": {"ticker": "AUTO", "cik_str": 123456},
                    "1": {"ticker": "OTHER", "cik_str": 654321},
                }
            ),
            SUBMISSIONS_URL: Response(submissions()),
            second_submissions_url: Response(submissions()),
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(
            user_agent="configured agent",
            max_submissions_cache_entries=1,
        ),
        client=client,
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("OTHER", NOW).status is CatalystStatus.TRUE
    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.TRUE
    assert [call[0] for call in client.calls] == [
        TICKERS_URL,
        SUBMISSIONS_URL,
        second_submissions_url,
        SUBMISSIONS_URL,
    ]


def test_ticker_resolution_normalizes_share_class_and_unknown_symbol_is_safe() -> None:
    provider, client = provider_for(
        Response(submissions()), ticker_response=Response(tickers(ticker="BRK-B"))
    )

    found = provider.get_evidence("brk.b", NOW)
    missing = provider.get_evidence("missing", NOW)
    invalid = provider.get_evidence("bad symbol!", NOW)

    assert found.status is CatalystStatus.TRUE
    assert found.symbol == "BRK-B"
    assert missing.status is CatalystStatus.FALSE
    assert invalid.status is CatalystStatus.FALSE
    assert [call[0] for call in client.calls].count(TICKERS_URL) == 1


def test_malformed_ticker_schema_returns_unknown() -> None:
    provider, _ = provider_for(
        Response(submissions()), ticker_response=Response({"0": {"ticker": "AUTO"}})
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN


def test_sec_policy_rejects_rates_above_fair_access_ceiling() -> None:
    with pytest.raises(ValueError, match="must not exceed 10"):
        SECEdgarPolicy(user_agent="configured agent", requests_per_second=10.1)


@pytest.mark.parametrize(
    "field",
    ("max_ticker_entries", "max_submissions_cache_entries"),
)
def test_sec_policy_requires_positive_cache_bounds(field: str) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        SECEdgarPolicy(user_agent="configured agent", **{field: 0})


def test_oversized_ticker_mapping_is_rejected_without_being_cached() -> None:
    client = Client(
        {
            TICKERS_URL: Response(
                {
                    "0": {"ticker": "AUTO", "cik_str": 123456},
                    "1": {"ticker": "OTHER", "cik_str": 654321},
                }
            ),
            SUBMISSIONS_URL: Response(submissions()),
        }
    )
    provider = SECEdgarCatalystProvider(
        SECEdgarPolicy(
            user_agent="configured agent",
            max_ticker_entries=1,
        ),
        client=client,
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )

    assert provider.get_evidence("AUTO", NOW).status is CatalystStatus.UNKNOWN
    assert [call[0] for call in client.calls] == [TICKERS_URL]


def test_configuration_disables_sec_without_user_agent_and_enables_when_present() -> None:
    disabled = load_configuration({})
    enabled = load_configuration(
        {
            "SEC_EDGAR_USER_AGENT": "configured agent contact@example.invalid",
            "SEC_EDGAR_FRESHNESS_DAYS": "2",
            "SEC_EDGAR_TIMEOUT_SECONDS": "4.5",
        }
    )

    assert disabled.sec_edgar is None
    assert enabled.sec_edgar is not None
    assert enabled.sec_edgar.user_agent == "configured agent contact@example.invalid"
    assert enabled.sec_edgar.freshness_days == 2
    assert enabled.sec_edgar.timeout_seconds == 4.5


class WebullFundamentals:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def get_earnings_calendar(self, symbol: str, category: str) -> Response:
        if self.fail:
            raise PermissionError("Webull offline")
        return Response([])

    def get_sec_filings(self, symbol: str, category: str) -> Response:
        if self.fail:
            raise PermissionError("Webull offline")
        return Response([])


def test_sec_and_webull_provider_permutations_are_identical() -> None:
    sec, _ = provider_for(Response(submissions()))
    webull = WebullCatalystProvider(
        SimpleNamespace(fundamentals=WebullFundamentals())
    )

    results = tuple(
        CatalystAggregator(ordering).aggregate_result("AUTO", NOW)
        for ordering in permutations((sec, webull))
    )

    assert results[0] == results[1]
    assert results[0].selected.source == "SEC_EDGAR"
    assert results[0].selected.status is CatalystStatus.TRUE


def test_sec_outage_does_not_erase_webull_success() -> None:
    sec, _ = provider_for(Response({}, status_code=429))

    class PositiveWebullFundamentals(WebullFundamentals):
        def get_sec_filings(self, symbol: str, category: str) -> Response:
            return Response([{"filing_date": "2026-07-30", "title": "8-K"}])

    webull = WebullCatalystProvider(
        SimpleNamespace(fundamentals=PositiveWebullFundamentals())
    )

    result = CatalystAggregator((sec, webull)).aggregate_result("AUTO", NOW)

    assert result.selected.source == "WEBULL_SEC_FILINGS"
    assert result.selected.status is CatalystStatus.TRUE
    assert any(item.status is CatalystStatus.UNAVAILABLE for item in result.evidence)


def test_webull_outage_does_not_erase_sec_success() -> None:
    sec, _ = provider_for(Response(submissions()))
    webull = WebullCatalystProvider(
        SimpleNamespace(fundamentals=WebullFundamentals(fail=True))
    )

    result = CatalystAggregator((webull, sec)).aggregate_result("AUTO", NOW)

    assert result.selected.source == "SEC_EDGAR"
    assert result.selected.status is CatalystStatus.TRUE
    assert any(item.status is CatalystStatus.UNAVAILABLE for item in result.evidence)


@dataclass
class CorroboratingProvider:
    name: str
    item: CatalystEvidence

    def get_evidence(self, symbol: str, as_of: datetime | None = None) -> CatalystEvidence:
        return self.item


def test_sec_canonical_event_supports_cross_source_deduplication() -> None:
    sec, _ = provider_for(Response(submissions()))
    sec_item = sec.get_evidence("AUTO", NOW)
    corroboration = CatalystEvidence(
        symbol="AUTO",
        catalyst_type=CatalystType.SEC_FILING,
        status=CatalystStatus.TRUE,
        headline="AUTO files current report",
        source="CORROBORATING_SOURCE",
        published_at=NOW,
        provider_event_id="story-1",
        canonical_event_id=sec_item.canonical_event_id,
    )

    result = CatalystAggregator(
        (sec, CorroboratingProvider("CORROBORATING_SOURCE", corroboration))
    ).aggregate_result("AUTO", NOW)

    assert len(result.events) == 1
    assert result.events[0].sources == ("CORROBORATING_SOURCE", "SEC_EDGAR")
    assert len(result.events[0].evidence) == 2
