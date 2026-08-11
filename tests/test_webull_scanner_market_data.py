from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.live_scanner.session import ScannerSession, scanner_session
from app.market_data.models import MarketEventType
from app.momentum_scanner import AssetClass, CatalystStatus, CatalystType
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
    assert reference.catalyst_status is CatalystStatus.TRUE
    assert reference.catalyst_headline == "Q2 earnings"


def test_catalyst_failure_is_unavailable_not_a_valid_negative() -> None:
    class UnavailableFundamentals:
        def get_earnings_calendar(self, symbol, category):
            raise PermissionError("fundamentals unavailable")

    client = SimpleNamespace(
        screener=Screener(),
        fundamentals=UnavailableFundamentals(),
        market_data=MarketData(),
    )
    lazy = LazyOfficialDataClient(lambda: client)
    universe = WebullScannerUniverseProvider(lazy, clock=lambda: NOW)
    universe.list_symbols(AssetClass.STOCK)

    reference = WebullScannerReferenceProvider(
        lazy,
        universe,
        clock=lambda: NOW,
    ).get_reference_data("AUTO", AssetClass.STOCK)

    assert reference.catalyst is CatalystType.NONE
    assert reference.catalyst_status is CatalystStatus.UNAVAILABLE


def _reference_with_fundamentals(fundamentals) -> object:
    client = SimpleNamespace(
        screener=Screener(),
        fundamentals=fundamentals,
        market_data=MarketData(),
    )
    lazy = LazyOfficialDataClient(lambda: client)
    universe = WebullScannerUniverseProvider(lazy, clock=lambda: NOW)
    universe.list_symbols(AssetClass.STOCK)
    return WebullScannerReferenceProvider(
        lazy,
        universe,
        clock=lambda: NOW,
    ).get_reference_data("AUTO", AssetClass.STOCK)


def test_production_earnings_expected_publish_date_is_true() -> None:
    class ProductionEarningsFundamentals:
        def get_earnings_calendar(self, symbol, category):
            return Response([{
                "expected_publish_date": "2026-07-29T23:30:00-05:00",
                "fiscal_period": 2,
                "fiscal_year": 2026,
            }])

        def get_sec_filings(self, symbol, category):
            raise AssertionError("matching earnings catalyst should win")

    reference = _reference_with_fundamentals(
        ProductionEarningsFundamentals()
    )

    assert reference.catalyst is CatalystType.EARNINGS
    assert reference.catalyst_status is CatalystStatus.TRUE
    assert reference.catalyst_headline == "Earnings"


def test_production_sec_filings_container_and_publish_date_are_true() -> None:
    class ProductionFilingsFundamentals:
        def get_earnings_calendar(self, symbol, category):
            return Response([{"expected_publish_date": "2026-06-01"}])

        def get_sec_filings(self, symbol, category):
            return Response({
                "category": category,
                "filings": [{
                    "publish_date": "2026-07-30T01:00:00Z",
                    "title": "8-K | Material event",
                }],
                "symbol": symbol,
            })

    reference = _reference_with_fundamentals(
        ProductionFilingsFundamentals()
    )

    assert reference.catalyst is CatalystType.SEC_FILING
    assert reference.catalyst_status is CatalystStatus.TRUE
    assert reference.catalyst_headline == "8-K | Material event"


def test_valid_production_responses_without_recent_event_are_false() -> None:
    class OldEvidenceFundamentals:
        def get_earnings_calendar(self, symbol, category):
            return Response([{"expected_publish_date": "2026-06-01"}])

        def get_sec_filings(self, symbol, category):
            return Response({
                "category": category,
                "filings": [{"publish_date": "2026-06-02"}],
                "symbol": symbol,
            })

    reference = _reference_with_fundamentals(OldEvidenceFundamentals())

    assert reference.catalyst is CatalystType.NONE
    assert reference.catalyst_status is CatalystStatus.FALSE


def test_reachable_unsupported_catalyst_schema_is_unknown() -> None:
    class UnsupportedSchemaFundamentals:
        def get_earnings_calendar(self, symbol, category):
            return Response({"results": []})

        def get_sec_filings(self, symbol, category):
            return Response({"results": []})

    reference = _reference_with_fundamentals(
        UnsupportedSchemaFundamentals()
    )

    assert reference.catalyst is CatalystType.NONE
    assert reference.catalyst_status is CatalystStatus.UNKNOWN


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
