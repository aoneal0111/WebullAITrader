from datetime import date, datetime
from zoneinfo import ZoneInfo

import app.market.calendar as market_calendar
from app.market.calendar import MarketSession, market_session, trading_day_schedule

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


def test_trading_day_schedule_caches_same_date(monkeypatch):
    market_calendar._trading_day_schedule_for_date.cache_clear()

    calls = 0
    original_schedule = market_calendar.NYSE.schedule

    def counting_schedule(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_schedule(*args, **kwargs)

    monkeypatch.setattr(
        market_calendar.NYSE,
        "schedule",
        counting_schedule,
    )

    first = trading_day_schedule(date(2026, 7, 20))
    second = trading_day_schedule(
        datetime(2026, 7, 20, 15, 30, tzinfo=ET)
    )

    assert first == second
    assert calls == 1


def test_trading_day_schedule_caches_closed_date(monkeypatch):
    market_calendar._trading_day_schedule_for_date.cache_clear()

    calls = 0
    original_schedule = market_calendar.NYSE.schedule

    def counting_schedule(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_schedule(*args, **kwargs)

    monkeypatch.setattr(
        market_calendar.NYSE,
        "schedule",
        counting_schedule,
    )

    assert trading_day_schedule(date(2026, 7, 19)) is None
    assert trading_day_schedule(date(2026, 7, 19)) is None
    assert calls == 1


def test_trading_day_schedule_caches_dates_independently(monkeypatch):
    market_calendar._trading_day_schedule_for_date.cache_clear()

    calls = 0
    original_schedule = market_calendar.NYSE.schedule

    def counting_schedule(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_schedule(*args, **kwargs)

    monkeypatch.setattr(
        market_calendar.NYSE,
        "schedule",
        counting_schedule,
    )

    assert trading_day_schedule(date(2026, 7, 20)) is not None
    assert trading_day_schedule(date(2026, 7, 21)) is not None
    assert trading_day_schedule(date(2026, 7, 20)) is not None

    assert calls == 2
