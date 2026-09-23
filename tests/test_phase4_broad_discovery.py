from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.momentum_scanner import AssetClass
from app.webull.sdk_market_data import LazyOfficialDataClient, WebullScannerUniverseProvider


NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return {"data": self.rows}


def row(symbol: str, rank: int) -> dict[str, str]:
    return {
        "symbol": symbol, "exchange_code": "NSQ", "currency_code": "USD",
        "price": str(5 + rank), "pre_close": "5", "volume": "1000000",
        "relative_volume_10d": "5", "market_value": "50000000",
    }


class PagedScreener:
    def __init__(self):
        self.calls = []
        self.generation = 0

    def get_gainers_losers(self, *args, **kwargs):
        self.calls.append(("GAINERS", kwargs.copy()))
        page = kwargs["page_index"]
        start = (page - 1) * kwargs["page_size"]
        return Response([row(f"G{index:03d}", index) for index in range(start, start + kwargs["page_size"])])

    def get_most_active(self, *args, **kwargs):
        self.calls.append((kwargs["sort_by"], kwargs.copy()))
        page = kwargs["page_index"]
        start = (page - 1) * kwargs["page_size"]
        prefix = "R" if kwargs["sort_by"] == "RELATIVE_VOLUME_10D" else "T"
        overlap = [row("G001", 1)] if page == 1 else []
        return Response(overlap + [row(f"{prefix}{index:03d}", index) for index in range(start, start + kwargs["page_size"] - len(overlap))])


def provider(screener, clock=lambda: NOW, **kwargs):
    client = LazyOfficialDataClient(lambda: SimpleNamespace(screener=screener))
    return WebullScannerUniverseProvider(client, clock=clock, **kwargs)


def test_sources_pages_are_unioned_and_deduplicated():
    screener = PagedScreener()
    result = provider(
        screener, page_size=2, maximum_breadth=4,
        sources=("SESSION_GAINERS", "RELATIVE_VOLUME_10D", "TURNOVER_LEADERS"),
    ).list_symbols(AssetClass.STOCK)
    symbols = {item.symbol for item in result}
    assert len(symbols) == len(result)
    assert {"G000", "G001", "G002", "G003", "R002", "T002"} <= symbols
    assert max(call[1]["page_index"] for call in screener.calls) == 2


def test_falling_off_source_is_retained_then_expires():
    now = [NOW]

    class Changing(PagedScreener):
        def get_gainers_losers(self, *args, **kwargs):
            if now[0] > NOW:
                return Response([row("NEW", 1)])
            return Response([row("OLD", 1)])

    screener = Changing()
    selected = provider(
        screener, clock=lambda: now[0], page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS",), retention_seconds=300,
    )
    assert {item.symbol for item in selected.list_symbols(AssetClass.STOCK)} == {"OLD"}
    now[0] = NOW + timedelta(seconds=60)
    assert {item.symbol for item in selected.list_symbols(AssetClass.STOCK)} == {"NEW", "OLD"}
    now[0] = NOW + timedelta(seconds=301)
    assert {item.symbol for item in selected.list_symbols(AssetClass.STOCK)} == {"NEW"}
    assert selected.metrics()["expired_symbols"] == 1


def test_broad_discovery_does_not_expand_subscription_budget():
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._maximum_subscription_channels = 3
    coordinator._retained_channels_source = lambda: ("POS",)
    selected = coordinator._effective_channels(("G", "R", "T", "X"))
    assert selected == ("G", "POS", "R")
    assert len(selected) == 3


def test_source_failure_does_not_discard_other_source_rows():
    class Partial(PagedScreener):
        def get_most_active(self, *args, **kwargs):
            raise TimeoutError("offline fake")

    result = provider(
        Partial(), page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS", "RELATIVE_VOLUME_10D"),
    ).list_symbols(AssetClass.STOCK)
    assert {item.symbol for item in result} == {"G000", "G001"}


def test_discovery_metrics_are_bounded_and_report_pages():
    selected = provider(
        PagedScreener(), page_size=2, maximum_breadth=4,
        sources=("SESSION_GAINERS", "RELATIVE_VOLUME_10D"),
    )
    selected.list_symbols(AssetClass.STOCK)
    metrics = selected.metrics()
    assert metrics["refresh_count"] == 1
    assert metrics["pages"] == 4
    assert metrics["unique_symbols"] <= 8
