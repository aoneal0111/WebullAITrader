from datetime import datetime
from zoneinfo import ZoneInfo

from app.market.calendar import MarketSession, market_session

ET = ZoneInfo("America/New_York")


def test_core_session():
    assert (
        market_session(datetime(2026, 7, 20, 10, 0, tzinfo=ET))
        is MarketSession.CORE
    )


def test_premarket():
    assert (
        market_session(datetime(2026, 7, 20, 8, 0, tzinfo=ET))
        is MarketSession.PREMARKET
    )


def test_after_hours():
    assert (
        market_session(datetime(2026, 7, 20, 17, 0, tzinfo=ET))
        is MarketSession.AFTER_HOURS
    )


def test_weekend_closed():
    assert (
        market_session(datetime(2026, 7, 19, 12, 0, tzinfo=ET))
        is MarketSession.CLOSED
    )


def test_christmas_closed():
    assert (
        market_session(datetime(2026, 12, 25, 10, 0, tzinfo=ET))
        is MarketSession.CLOSED
    )