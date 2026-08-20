from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from itertools import permutations
from types import SimpleNamespace

import pytest

from app.catalysts import (
    DEFAULT_CATALYST_PRIORITY_POLICY,
    CatalystAggregator,
    CatalystEvidence,
    CatalystPriorityPolicy,
    CatalystProvider,
    CatalystStatus,
    CatalystType,
    WebullCatalystProvider,
)


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, value: object) -> None:
        self._value = value

    def json(self) -> object:
        return self._value


@dataclass
class StubProvider:
    name: str
    evidence: CatalystEvidence | None = None
    error: Exception | None = None
    calls: int = 0

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert symbol == "AUTO"
        assert as_of == NOW
        assert self.evidence is not None
        return self.evidence


def evidence(
    status: CatalystStatus,
    *,
    catalyst_type: CatalystType = CatalystType.NONE,
    headline: str | None = None,
    source: str = "STUB",
    published_at: datetime | None = None,
    source_url: str | None = None,
    provider_event_id: str | None = None,
    canonical_event_id: str | None = None,
) -> CatalystEvidence:
    return CatalystEvidence(
        symbol="AUTO",
        catalyst_type=catalyst_type,
        status=status,
        headline=headline,
        source=source,
        published_at=published_at,
        source_url=source_url,
        provider_event_id=provider_event_id,
        canonical_event_id=canonical_event_id,
    )


def provider(item: CatalystEvidence, name: str | None = None) -> StubProvider:
    return StubProvider(name or item.source, item)


def test_evidence_is_immutable_and_exposes_legacy_scanner_fields() -> None:
    item = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="Q2 earnings",
    )

    assert item.catalyst is CatalystType.EARNINGS
    assert item.as_scanner_fields() == (
        CatalystType.EARNINGS,
        "Q2 earnings",
        CatalystStatus.TRUE,
    )
    with pytest.raises(FrozenInstanceError):
        item.headline = "changed"  # type: ignore[misc]


def test_evidence_uses_stable_identity_without_discarding_corroboration() -> None:
    first = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="Company reports earnings",
        source="CNBC",
        published_at=NOW,
        source_url="https://example.invalid/cnbc",
        provider_event_id="cnbc-1",
        canonical_event_id="AUTO-Q2-2026",
    )
    second = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="AUTO releases second-quarter results",
        source="YAHOO",
        published_at=NOW + timedelta(minutes=2),
        source_url="https://example.invalid/yahoo",
        provider_event_id="yahoo-9",
        canonical_event_id="AUTO-Q2-2026",
    )

    assert first.event_identity == second.event_identity
    assert first.evidence_identity != second.evidence_identity
    assert first.event_identity == first.event_identity
    assert len(first.event_identity.removeprefix("catalyst-event-")) == 64


def test_provider_protocol_and_priority_table_cover_existing_types() -> None:
    item = provider(evidence(CatalystStatus.FALSE))

    assert isinstance(item, CatalystProvider)
    assert set(DEFAULT_CATALYST_PRIORITY_POLICY.ordered_types) == set(CatalystType)
    assert (
        DEFAULT_CATALYST_PRIORITY_POLICY.priority(CatalystType.EARNINGS)
        > DEFAULT_CATALYST_PRIORITY_POLICY.priority(CatalystType.SEC_FILING)
        > DEFAULT_CATALYST_PRIORITY_POLICY.priority(CatalystType.NONE)
    )


def test_custom_priority_policy_changes_selection_deterministically() -> None:
    earnings = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="Earnings",
        source="EARNINGS",
    )
    filing = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.SEC_FILING,
        headline="8-K",
        source="SEC",
    )
    custom_order = (
        CatalystType.SEC_FILING,
        CatalystType.EARNINGS,
        *(
            item
            for item in DEFAULT_CATALYST_PRIORITY_POLICY.ordered_types
            if item not in {CatalystType.EARNINGS, CatalystType.SEC_FILING}
        ),
    )
    custom_policy = CatalystPriorityPolicy(custom_order)

    results = tuple(
        CatalystAggregator(
            tuple(provider(item) for item in ordering),
            priority_policy=custom_policy,
        ).get_evidence("AUTO", NOW)
        for ordering in ((earnings, filing), (filing, earnings))
    )

    assert results[0] == results[1]
    assert results[0].catalyst_type is CatalystType.SEC_FILING


def test_provider_permutations_produce_identical_results() -> None:
    items = (
        evidence(
            CatalystStatus.TRUE,
            catalyst_type=CatalystType.SEC_FILING,
            headline="8-K",
            source="SEC_SOURCE",
            published_at=NOW,
        ),
        evidence(
            CatalystStatus.TRUE,
            catalyst_type=CatalystType.EARNINGS,
            headline="Earnings",
            source="EARNINGS_SOURCE",
            published_at=NOW - timedelta(hours=1),
        ),
        evidence(CatalystStatus.UNKNOWN, source="UNKNOWN_SOURCE"),
    )

    results = tuple(
        CatalystAggregator(tuple(provider(item) for item in ordering))
        .aggregate_result("AUTO", NOW)
        for ordering in permutations(items)
    )

    assert all(result == results[0] for result in results)
    assert results[0].selected.catalyst_type is CatalystType.EARNINGS
    assert all(len(result.evidence) == 3 for result in results)


def test_earnings_beats_sec_in_reversed_provider_order() -> None:
    earnings = provider(
        evidence(
            CatalystStatus.TRUE,
            catalyst_type=CatalystType.EARNINGS,
            headline="Earnings",
            source="EARNINGS",
        )
    )
    filing = provider(
        evidence(
            CatalystStatus.TRUE,
            catalyst_type=CatalystType.SEC_FILING,
            headline="8-K",
            source="SEC",
        )
    )

    forward = CatalystAggregator((earnings, filing)).get_evidence("AUTO", NOW)
    reverse = CatalystAggregator((filing, earnings)).get_evidence("AUTO", NOW)

    assert forward == reverse
    assert forward.catalyst_type is CatalystType.EARNINGS


def test_provider_failure_does_not_erase_true_evidence_in_either_order() -> None:
    positive_item = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.SEC_FILING,
        headline="8-K",
        source="WORKING",
    )

    results = []
    for failing_first in (True, False):
        failing = StubProvider("OFFLINE", error=PermissionError("offline"))
        working = provider(positive_item)
        providers = (failing, working) if failing_first else (working, failing)
        result = CatalystAggregator(providers).aggregate_result("AUTO", NOW)
        results.append(result)
        assert failing.calls == 1
        assert working.calls == 1

    assert results[0] == results[1]
    assert results[0].selected == positive_item
    assert any(
        item.status is CatalystStatus.UNAVAILABLE
        for item in results[0].evidence
    )


def test_exact_duplicate_evidence_is_deduplicated() -> None:
    item = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="Q2 earnings",
        source="YAHOO",
        published_at=NOW,
        provider_event_id="event-1",
    )

    result = CatalystAggregator(
        (provider(item, "YAHOO_A"), provider(item, "YAHOO_B"))
    ).aggregate_result("AUTO", NOW)

    assert result.selected == item
    assert result.evidence == (item,)
    assert len(result.events) == 1
    assert result.events[0].evidence == (item,)


def test_same_event_retains_distinct_corroborating_sources() -> None:
    cnbc = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="AUTO reports earnings",
        source="CNBC",
        published_at=NOW,
        provider_event_id="cnbc-1",
        canonical_event_id="auto-earnings-2026-q2",
    )
    benzinga = evidence(
        CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS,
        headline="AUTO Q2 results",
        source="BENZINGA",
        published_at=NOW + timedelta(minutes=1),
        provider_event_id="benzinga-7",
        canonical_event_id="auto-earnings-2026-q2",
    )

    result = CatalystAggregator(
        (provider(cnbc), provider(benzinga))
    ).aggregate_result("AUTO", NOW)

    assert len(result.events) == 1
    assert result.events[0].sources == ("BENZINGA", "CNBC")
    assert set(result.events[0].evidence) == {cnbc, benzinga}
    assert len(result.evidence) == 2


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((CatalystStatus.TRUE, CatalystStatus.FALSE), CatalystStatus.TRUE),
        ((CatalystStatus.TRUE, CatalystStatus.UNKNOWN), CatalystStatus.TRUE),
        ((CatalystStatus.FALSE, CatalystStatus.FALSE), CatalystStatus.FALSE),
        ((CatalystStatus.FALSE, CatalystStatus.UNKNOWN), CatalystStatus.UNKNOWN),
        (
            (CatalystStatus.UNAVAILABLE, CatalystStatus.UNAVAILABLE),
            CatalystStatus.UNAVAILABLE,
        ),
    ],
)
def test_status_resolution_is_order_independent(
    statuses: tuple[CatalystStatus, ...],
    expected: CatalystStatus,
) -> None:
    def item(index: int, status: CatalystStatus) -> CatalystEvidence:
        return evidence(
            status,
            catalyst_type=(
                CatalystType.OTHER
                if status is CatalystStatus.TRUE
                else CatalystType.NONE
            ),
            headline="Catalyst" if status is CatalystStatus.TRUE else None,
            source=f"SOURCE_{index}",
        )

    results = tuple(
        CatalystAggregator(
            tuple(provider(item(index, status)) for index, status in ordering)
        ).get_evidence("AUTO", NOW)
        for ordering in permutations(tuple(enumerate(statuses)))
    )

    assert all(result == results[0] for result in results)
    assert results[0].status is expected


def test_empty_provider_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one catalyst provider"):
        CatalystAggregator(())


def test_repeated_runs_produce_identical_output() -> None:
    items = (
        evidence(CatalystStatus.FALSE, source="B"),
        evidence(CatalystStatus.UNKNOWN, source="A"),
    )
    aggregator = CatalystAggregator(tuple(provider(item) for item in items))

    results = tuple(aggregator.aggregate_result("AUTO", NOW) for _ in range(20))

    assert all(result == results[0] for result in results)


def test_dependency_setup_failure_is_intentionally_isolated() -> None:
    webull = WebullCatalystProvider(SimpleNamespace())

    result = CatalystAggregator((webull,)).aggregate_result("AUTO", NOW)

    assert result.selected.status is CatalystStatus.UNAVAILABLE
    assert result.selected.catalyst_type is CatalystType.NONE
    assert result.evidence[0].source == WebullCatalystProvider.name


def test_webull_provider_keeps_earnings_first_and_skips_filings() -> None:
    class Fundamentals:
        def get_earnings_calendar(self, symbol: str, category: str) -> Response:
            assert (symbol, category) == ("AUTO", "US_STOCK")
            return Response(
                {
                    "data": [
                        {
                            "report_date": "2026-07-30",
                            "title": "Q2",
                            "event_id": "earnings-2",
                        }
                    ]
                }
            )

        def get_sec_filings(self, symbol: str, category: str) -> Response:
            raise AssertionError("earnings evidence must retain precedence")

    provider_instance = WebullCatalystProvider(
        SimpleNamespace(fundamentals=Fundamentals())
    )

    result = provider_instance.get_evidence("auto", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.EARNINGS,
        "Q2",
        CatalystStatus.TRUE,
    )
    assert result.symbol == "AUTO"
    assert result.published_at == datetime(2026, 7, 30, tzinfo=UTC)
    assert result.provider_event_id == "earnings-2"


@pytest.mark.parametrize(
    ("event_kind", "age_days", "expected"),
    [
        (CatalystType.EARNINGS, 2, CatalystStatus.TRUE),
        (CatalystType.EARNINGS, 3, CatalystStatus.FALSE),
        (CatalystType.SEC_FILING, 3, CatalystStatus.TRUE),
        (CatalystType.SEC_FILING, 4, CatalystStatus.FALSE),
    ],
)
def test_webull_freshness_windows_are_preserved(
    event_kind: CatalystType,
    age_days: int,
    expected: CatalystStatus,
) -> None:
    event_date = (NOW - timedelta(days=age_days)).date().isoformat()

    class Fundamentals:
        def get_earnings_calendar(self, symbol: str, category: str) -> Response:
            rows = (
                [{"report_date": event_date}]
                if event_kind is CatalystType.EARNINGS
                else [{"report_date": "2026-06-01"}]
            )
            return Response(rows)

        def get_sec_filings(self, symbol: str, category: str) -> Response:
            rows = (
                [{"filing_date": event_date}]
                if event_kind is CatalystType.SEC_FILING
                else [{"filing_date": "2026-06-01"}]
            )
            return Response(rows)

    result = WebullCatalystProvider(
        SimpleNamespace(fundamentals=Fundamentals())
    ).get_evidence("AUTO", NOW)

    assert result.status is expected
    assert result.catalyst_type is (
        event_kind if expected is CatalystStatus.TRUE else CatalystType.NONE
    )


def test_webull_sec_filing_behavior_is_preserved() -> None:
    class Fundamentals:
        def get_earnings_calendar(self, symbol: str, category: str) -> Response:
            return Response([{"report_date": "2026-06-01"}])

        def get_sec_filings(self, symbol: str, category: str) -> Response:
            return Response(
                {
                    "filings": [
                        {
                            "publish_date": "2026-07-30T01:00:00Z",
                            "title": "8-K | Material event",
                            "accession_number": "0001",
                            "filing_url": "https://example.invalid/filing",
                        }
                    ]
                }
            )

    result = WebullCatalystProvider(
        SimpleNamespace(fundamentals=Fundamentals())
    ).get_evidence("AUTO", NOW)

    assert result.as_scanner_fields() == (
        CatalystType.SEC_FILING,
        "8-K | Material event",
        CatalystStatus.TRUE,
    )
    assert result.provider_event_id == "0001"
    assert result.source_url == "https://example.invalid/filing"


def test_webull_false_unknown_and_unavailable_semantics_are_preserved() -> None:
    class Fundamentals:
        def __init__(self, earnings: object, filings: object) -> None:
            self.earnings = earnings
            self.filings = filings

        def get_earnings_calendar(self, symbol: str, category: str) -> Response:
            if isinstance(self.earnings, Exception):
                raise self.earnings
            return Response(self.earnings)

        def get_sec_filings(self, symbol: str, category: str) -> Response:
            return Response(self.filings)

    cases = (
        (
            Fundamentals(
                [{"report_date": "2026-06-01"}],
                [{"filing_date": "2026-06-02"}],
            ),
            CatalystStatus.FALSE,
        ),
        (Fundamentals({"results": []}, {"results": []}), CatalystStatus.UNKNOWN),
        (
            Fundamentals(PermissionError("unavailable"), []),
            CatalystStatus.UNAVAILABLE,
        ),
    )

    results = tuple(
        WebullCatalystProvider(
            SimpleNamespace(fundamentals=fundamentals)
        ).get_evidence("AUTO", NOW)
        for fundamentals, _ in cases
    )

    assert tuple(item.status for item in results) == tuple(
        expected for _, expected in cases
    )
    assert all(item.catalyst_type is CatalystType.NONE for item in results)
