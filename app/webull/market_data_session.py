"""Clock-driven U.S. equity session classification for market data."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Callable

from app.live_scanner.session import ScannerSession, scanner_session
from app.market.calendar import EASTERN, is_trading_day


class MarketDataSession(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    OVERNIGHT = "OVERNIGHT"
    CLOSED = "CLOSED"


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def current_market_data_session(clock: Clock = utc_now) -> MarketDataSession:
    """Classify an injected instant after normalizing it to New York time."""

    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("market-data clock must return datetime")
    session = scanner_session(value)
    return {
        ScannerSession.PREMARKET: MarketDataSession.PREMARKET,
        ScannerSession.REGULAR: MarketDataSession.REGULAR,
        ScannerSession.AFTER_HOURS: MarketDataSession.AFTER_HOURS,
        ScannerSession.OVERNIGHT: MarketDataSession.OVERNIGHT,
        ScannerSession.PREPARATION: MarketDataSession.CLOSED,
    }[session]


def requires_overnight_entitlement(clock: Clock = utc_now) -> bool:
    return current_market_data_session(clock) is MarketDataSession.OVERNIGHT


def next_premarket_start(clock: Clock = utc_now) -> datetime | None:
    """Return the next 04:00 New York instant after an overnight session."""

    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("market-data clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scanner session datetime must be timezone-aware")
    current = value.astimezone(EASTERN)
    if current_market_data_session(lambda: value) is not MarketDataSession.OVERNIGHT:
        return None
    candidate = current.date()
    if current.time() >= time(4):
        candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return datetime.combine(candidate, time(4), tzinfo=EASTERN)


__all__ = [
    "Clock",
    "MarketDataSession",
    "current_market_data_session",
    "next_premarket_start",
    "requires_overnight_entitlement",
    "utc_now",
]
