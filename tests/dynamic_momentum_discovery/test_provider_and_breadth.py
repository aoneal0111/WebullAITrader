from __future__ import annotations

from app.dynamic_momentum_discovery import DiscoverySource, WebullBroadDiscoveryProvider
from app.dynamic_momentum_discovery.experiments import summarize_breadths
from tests.dynamic_momentum_discovery.helpers import NOW


class Response:
    def __init__(self, rows, has_more):
        self.rows = rows
        self.has_more = has_more

    def json(self):
        return {"data": self.rows, "has_more": self.has_more}


def _row(symbol, rank):
    return {
        "symbol": symbol, "price": str(5 + rank / 1000), "pre_close": "4",
        "open": "4.5",
        "high": "6", "volume": str(100000 + rank),
        "relative_volume_10d": "3", "turnover": "1000000",
    }


class PagedScreener:
    def __init__(self, total=500, fail_source=None):
        self.total = total
        self.fail_source = fail_source
        self.calls = []

    def get_gainers_losers(self, *args, **kwargs):
        self.calls.append(("GAINERS", args, kwargs))
        if self.fail_source == "GAINERS":
            raise RuntimeError("provider unavailable")
        return self._page("G", kwargs)

    def get_most_active(self, *args, **kwargs):
        source = kwargs["sort_by"]
        self.calls.append((source, args, kwargs))
        if self.fail_source == source:
            raise RuntimeError("provider unavailable")
        prefix = {"RELATIVE_VOLUME_10D": "R", "VOLUME": "V", "TURNOVER": "T"}[source]
        return self._page(prefix, kwargs)

    def _page(self, prefix, kwargs):
        start = (kwargs["page_index"] - 1) * kwargs["page_size"]
        end = min(start + kwargs["page_size"], self.total)
        if prefix == "R":
            # Fifty-percent source overlap at each breadth boundary.
            rows = [_row(f"G{index + 25:04d}", index) for index in range(start, end)]
        else:
            rows = [_row(f"{prefix}{index:04d}", index) for index in range(start, end)]
        return Response(rows, end < self.total)


def test_sdk_research_provider_pages_without_changing_production_request_shape():
    screener = PagedScreener()
    provider = WebullBroadDiscoveryProvider(screener, page_size=50)
    refresh = provider.fetch(breadth_per_source=100, observed_at=NOW, session="REGULAR")
    assert refresh.request_count == 4
    assert refresh.returned_row_count == 200
    assert refresh.unique_symbol_count == 125
    assert all(call[2]["page_size"] == 50 for call in screener.calls)
    assert [call[2]["page_index"] for call in screener.calls] == [1, 2, 1, 2]
    assert screener.calls[0][1][:3] == ("DAY_1", "US_STOCK", "CHANGE_RATIO")
    assert screener.calls[2][2]["sort_by"] == "RELATIVE_VOLUME_10D"


def test_partial_breadth_keeps_fixed_page_shape_and_does_not_overlap_offsets():
    screener = PagedScreener()
    refresh = WebullBroadDiscoveryProvider(screener, page_size=50).fetch(
        breadth_per_source=75, observed_at=NOW, session="REGULAR"
    )
    assert all(call[2]["page_size"] == 50 for call in screener.calls)
    assert [call[2]["page_index"] for call in screener.calls] == [1, 2, 1, 2]
    assert refresh.returned_row_count == 200
    assert len(refresh.rows) == 150


def test_breadths_50_100_200_500_report_incremental_symbols_and_cost():
    refreshes = tuple(
        WebullBroadDiscoveryProvider(PagedScreener(), page_size=50).fetch(
            breadth_per_source=breadth, observed_at=NOW, session="PREMARKET"
        ) for breadth in (50, 100, 200, 500)
    )
    production = frozenset(row.symbol for row in refreshes[0].rows)
    results = summarize_breadths(refreshes, production_symbols=production)
    assert [(item.breadth_per_source, item.unique_symbols,
             item.incremental_symbols, item.request_count) for item in results] == [
        (50, 75, 0, 2), (100, 125, 50, 4),
        (200, 225, 150, 8), (500, 525, 450, 20),
    ]
    assert all(item.rows_per_request == 50 for item in results)


def test_source_provenance_rank_and_deduplication_are_preserved():
    refresh = WebullBroadDiscoveryProvider(PagedScreener()).fetch(
        breadth_per_source=50, observed_at=NOW, session="REGULAR"
    )
    overlapping = [row for row in refresh.rows if row.symbol == "G0025"]
    assert [(row.membership.source, row.membership.rank, row.membership.page_index)
            for row in overlapping] == [
        (DiscoverySource.SESSION_GAINERS, 26, 1),
        (DiscoverySource.RELATIVE_VOLUME_10D, 1, 1),
    ]
    assert refresh.returned_row_count == 100
    assert refresh.unique_symbol_count == 75


def test_additional_sdk_activity_sorts_are_available_to_research_only():
    screener = PagedScreener()
    provider = WebullBroadDiscoveryProvider(
        screener, sources=(DiscoverySource.VOLUME_LEADERS,
                           DiscoverySource.TURNOVER_LEADERS),
    )
    provider.fetch(breadth_per_source=50, observed_at=NOW, session="REGULAR")
    assert [call[2]["sort_by"] for call in screener.calls] == ["VOLUME", "TURNOVER"]


def test_provider_failure_is_captured_without_escaping_other_source():
    refresh = WebullBroadDiscoveryProvider(
        PagedScreener(fail_source="GAINERS")
    ).fetch(breadth_per_source=50, observed_at=NOW, session="REGULAR")
    assert len(refresh.failures) == 1
    assert refresh.failures[0].source is DiscoverySource.SESSION_GAINERS
    assert refresh.unique_symbol_count == 50
