from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from decimal import Decimal

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.momentum_scanner import AssetClass
from app.momentum_radar import MomentumRadar, RadarConfig
from app.webull.sdk_market_data import LazyOfficialDataClient, WebullScannerUniverseProvider, _percent_value


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


def test_startup_seed_requests_only_first_page_of_legacy_priority_sources():
    screener = PagedScreener()
    selected = provider(
        screener, page_size=2, maximum_breadth=4,
        sources=(
            "SESSION_GAINERS", "RELATIVE_VOLUME_10D",
            "VOLUME_LEADERS", "TURNOVER_LEADERS",
        ),
    )

    result = selected.list_startup_symbols(AssetClass.STOCK)

    assert [(source, call["page_index"], call["page_size"]) for source, call in screener.calls] == [
        ("GAINERS", 1, 2),
        ("RELATIVE_VOLUME_10D", 1, 2),
    ]
    assert tuple(item.symbol for item in result) == ("G000", "G001", "R000")
    assert selected.priority_lanes().legacy_primary == ("G000", "G001", "R000")


def test_full_discovery_pagination_is_unchanged_after_startup_seed():
    screener = PagedScreener()
    selected = provider(
        screener, page_size=2, maximum_breadth=4,
        sources=(
            "SESSION_GAINERS", "RELATIVE_VOLUME_10D",
            "VOLUME_LEADERS", "TURNOVER_LEADERS",
        ),
    )
    selected.list_startup_symbols(AssetClass.STOCK)
    screener.calls.clear()

    selected.list_symbols(AssetClass.STOCK)

    assert [(source, call["page_index"]) for source, call in screener.calls] == [
        ("GAINERS", 1), ("GAINERS", 2),
        ("RELATIVE_VOLUME_10D", 1), ("RELATIVE_VOLUME_10D", 2),
        ("VOLUME", 1), ("VOLUME", 2),
        ("TURNOVER", 1), ("TURNOVER", 2),
    ]


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
    assert selected == ("POS", "G", "R")
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


def test_ten_percent_mover_survives_rank_dropout_without_extending_on_stale_rows():
    now = [NOW]

    class Changing:
        def get_gainers_losers(self, *args, **kwargs):
            symbol, price = ("MOVER", "5.5") if now[0] == NOW else ("NEW", "5.1")
            return Response([{**row(symbol, 0), "price": price}])

    selected = provider(
        Changing(), clock=lambda: now[0], page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS",), retention_seconds=300,
        mover_retention_seconds=3600, accelerator_capacity=1,
        radar=MomentumRadar(RadarConfig(minimum_promotion_change_percent=Decimal("10"))),
    )
    selected.list_symbols(AssetClass.STOCK)
    now[0] = NOW + timedelta(minutes=20)
    assert {item.symbol for item in selected.list_symbols(AssetClass.STOCK)} == {"MOVER", "NEW"}
    assert selected.priority_lanes().legacy_primary == ("NEW",)
    assert selected.priority_lanes().accelerator == ("MOVER",)
    assert selected.eligible_mover_symbols(("NEW", "MOVER")) == ("MOVER",)
    assert selected.metrics()["retained_ten_percent_movers"] == 1
    now[0] = NOW + timedelta(seconds=3600)
    selected.list_symbols(AssetClass.STOCK)
    assert selected.eligible_mover_symbols(("MOVER",)) == ()
    assert selected.row_for("MOVER") is None


def test_retained_mover_does_not_carry_across_market_session_boundary():
    now = [NOW.replace(hour=13, minute=29)]

    class Changing:
        def get_gainers_losers(self, *args, **kwargs):
            return Response([{**row("MOVER", 0), "price": "5.5"}]) if now[0].minute == 29 else Response([])

    selected = provider(
        Changing(), clock=lambda: now[0], page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS",), retention_seconds=0,
        mover_retention_seconds=3600,
    )
    selected.list_symbols(AssetClass.STOCK)
    assert selected.eligible_mover_symbols(("MOVER",)) == ("MOVER",)
    now[0] += timedelta(minutes=1)
    assert selected.eligible_mover_symbols(("MOVER",)) == ()
    assert selected.list_symbols(AssetClass.STOCK) == ()


def test_catalyst_priority_orders_qualified_movers_without_promoting_subthreshold_seed():
    class Screener:
        def get_gainers_losers(self, *args, **kwargs):
            return Response([{**row(symbol, 0), "price": price}
                             for symbol, price in (("BASE", "5.1"), ("MOVER", "5.5"),
                                                   ("NEWS", "5.6"), ("COLD", "5.45"))])

    selected = provider(
        Screener(), page_size=50, maximum_breadth=50,
        sources=("SESSION_GAINERS",), legacy_source_limit=1,
        accelerator_capacity=1, mover_retention_seconds=3600,
        radar=MomentumRadar(RadarConfig(minimum_promotion_change_percent=Decimal("10"))),
    )
    selected.set_accelerator_symbols_source(lambda: ("COLD", "NEWS"))
    selected.list_symbols(AssetClass.STOCK)
    assert selected.priority_lanes().accelerator == ("NEWS",)
    assert selected.eligible_mover_symbols(("COLD", "NEWS")) == ("NEWS",)


def test_screener_percent_units_do_not_confuse_ratio_or_dollar_change():
    assert _percent_value({"change_ratio": "0.10"}) == Decimal("10")
    assert _percent_value({"change_percent": "10", "change_ratio": "0.10"}) == Decimal("10")
    assert _percent_value({"change": "10"}) is None
    assert _percent_value({"change_ratio": "NaN"}) is None
    assert _percent_value({"price": "5.5", "pre_close": "5"}) == Decimal("10")


def test_pullback_below_threshold_retains_window_but_does_not_renew_it():
    now = [NOW]

    class Screener:
        def get_gainers_losers(self, *args, **kwargs):
            ratio = "0.10" if now[0] == NOW else "0.09"
            return Response([{**row("MOVER", 0), "change_ratio": ratio}])

    selected = provider(
        Screener(), clock=lambda: now[0], page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS",), mover_retention_seconds=3600,
    )
    selected.list_symbols(AssetClass.STOCK)
    now[0] += timedelta(minutes=30)
    selected.list_symbols(AssetClass.STOCK)
    assert selected.eligible_mover_symbols(("MOVER",)) == ("MOVER",)
    now[0] = NOW + timedelta(seconds=3600)
    selected.list_symbols(AssetClass.STOCK)
    assert selected.eligible_mover_symbols(("MOVER",)) == ()


def test_qualifying_startup_seed_survives_first_full_refresh_dropout():
    now = [NOW]

    class Screener:
        def get_gainers_losers(self, *args, **kwargs):
            return Response([{**row("SEED", 0), "change_ratio": "0.10"}]) if now[0] == NOW else Response([])

    selected = provider(
        Screener(), clock=lambda: now[0], page_size=2, maximum_breadth=2,
        sources=("SESSION_GAINERS",), mover_retention_seconds=3600,
    )
    selected.list_startup_symbols(AssetClass.STOCK)
    now[0] += timedelta(minutes=20)
    assert tuple(item.symbol for item in selected.list_symbols(AssetClass.STOCK)) == ("SEED",)
