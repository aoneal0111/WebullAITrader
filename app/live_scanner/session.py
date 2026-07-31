"""Session state used by the autonomous US-stock scanner."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import StrEnum

from app.market.calendar import EASTERN, is_trading_day, trading_day_schedule


class ScannerSession(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    OVERNIGHT = "OVERNIGHT"
    PREPARATION = "PREPARATION"


def scanner_session(value: datetime) -> ScannerSession:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scanner session datetime must be timezone-aware")

    current = value.astimezone(EASTERN)
    schedule = trading_day_schedule(current)
    current_time = current.time()

    if schedule is not None:
        if time(4) <= current_time < schedule.market_open.time():
            return ScannerSession.PREMARKET
        if schedule.market_open <= current < schedule.market_close:
            return ScannerSession.REGULAR
        if schedule.market_close.time() <= current_time < time(20):
            return ScannerSession.AFTER_HOURS
        if current_time < time(4):
            previous_date = current.date() - timedelta(days=1)
            if is_trading_day(previous_date) or current.weekday() == 0:
                return ScannerSession.OVERNIGHT

    if current_time >= time(20):
        next_date = current.date() + timedelta(days=1)
        if is_trading_day(next_date):
            return ScannerSession.OVERNIGHT

    return ScannerSession.PREPARATION


__all__ = ["ScannerSession", "scanner_session"]
