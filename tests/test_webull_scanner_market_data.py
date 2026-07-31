from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.live_scanner.session import ScannerSession, scanner_session
from app.market_data.models import MarketEventType
from app.momentum_scanner import AssetClass, CatalystType
from app.webull.market_event_parser import WebullMarketEventParser
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerReferenceProvider,
    WebullScannerUniverseProvider,
)


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, value) -> None:
        self.value = value

    def json(self):
        return self.value


class Screener:
    def get_gainers_losers(self, *args, **kwargs):
        return Response(
            {
                "data": [
                    {
                        "symbol": "AUTO",
                        "exchange_code": "NSQ",
                        "currency_code": "USD",
                        "price": "5",
                        "pre_close": "4",
                        "volume": "1400000",
                        "relative_volume_10d": "7",
                        "market_value": "75000000",
                    }
                ]
            }
        )

    def get_most_active(self, *args, **kwargs):
        return Response({"data": []})


class Fundamentals:
    def get_earnings_calendar(self, symbol, category):
        return Response(
            {"data": [{"report_date": "2026-07-30", "title": "Q2 earnings"}]}
        )

    def get_sec_filings(self, symbol, category):
        raise AssertionError("earnings catalyst should win")


class MarketData:
    def get_history_bar(self, *args, **kwargs):
        return Response(
            [{"volume": "100000"}, {"volume": "300000"}]
        )


def test_official_sdk_providers_discover_and_load_reference_data() -> None:
    client = SimpleNamespace(
        screener=Screener(),
        fundamentals=Fundamentals(),
        market_data=MarketData(),
    )
    lazy = LazyOfficialDataClient(lambda: client)
    universe = WebullScannerUniverseProvider(lazy, clock=lambda: NOW)

    symbols = universe.list_symbols(AssetClass.STOCK)
    reference = WebullScannerReferenceProvider(
        lazy,
        universe,
        clock=lambda: NOW,
    ).get_reference_data("AUTO", AssetClass.STOCK)

    assert tuple(item.symbol for item in symbols) == ("AUTO",)
    assert symbols[0].average_30_day_volume == Decimal("200000")
    assert reference.previous_close == Decimal("4")
    assert reference.float_shares == Decimal("15000000")
    assert reference.catalyst is CatalystType.EARNINGS
    assert reference.catalyst_headline == "Q2 earnings"


def test_official_sdk_quote_and_tick_objects_parse_without_json() -> None:
    parser = WebullMarketEventParser(clock=lambda: NOW)
    basic = SimpleNamespace(
        symbol="AUTO",
        timestamp=int(NOW.timestamp() * 1000),
    )
    quote = SimpleNamespace(
        basic=basic,
        bids=[SimpleNamespace(price=Decimal("4.99"), size=100)],
        asks=[SimpleNamespace(price=Decimal("5.01"), size=120)],
    )
    tick = SimpleNamespace(
        basic=basic,
        time=int(NOW.timestamp() * 1000),
        price=Decimal("5"),
        volume=200,
    )
    snapshot = SimpleNamespace(
        basic=basic,
        last_trade_time=int(NOW.timestamp() * 1000),
        price=Decimal("5"),
        volume=1_400_000,
    )

    quote_event = parser(("quote", quote))
    tick_event = parser(("tick", tick))
    snapshot_event = parser(("snapshot", snapshot))

    assert quote_event.event_type is MarketEventType.QUOTE
    assert quote_event.payload.bid == Decimal("4.99")
    assert tick_event.event_type is MarketEventType.TRADE
    assert tick_event.payload.price == Decimal("5")
    assert snapshot_event.payload.trade_id == "snapshot"
    assert snapshot_event.payload.size == Decimal("1400000")


def test_scanner_sessions_do_not_claim_weekend_overnight() -> None:
    friday_night = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    sunday_night = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)

    assert scanner_session(friday_night) is ScannerSession.PREPARATION
    assert scanner_session(sunday_night) is ScannerSession.OVERNIGHT
