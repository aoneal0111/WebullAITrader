from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal


EASTERN = ZoneInfo("America/New_York")
NYSE = mcal.get_calendar("NYSE")


class MarketSession(StrEnum):
    CLOSED = "CLOSED"
    OVERNIGHT = "OVERNIGHT"
    PREMARKET = "PREMARKET"
    CORE = "CORE"
    AFTER_HOURS = "AFTER_HOURS"


@dataclass(frozen=True, slots=True)
class TradingDaySchedule:
    trading_date: date
    market_open: datetime
    market_close: datetime

    @property
    def is_early_close(self) -> bool:
        return self.market_close.astimezone(EASTERN).time() < time(16, 0)


def _eastern_datetime(value: datetime | None = None) -> datetime:
    current = value or datetime.now(EASTERN)

    if current.tzinfo is None:
        raise ValueError("market calendar datetime must be timezone-aware")

    return current.astimezone(EASTERN)


def trading_day_schedule(
    value: datetime | date,
) -> TradingDaySchedule | None:
    trading_date = value.date() if isinstance(value, datetime) else value

    schedule = NYSE.schedule(
        start_date=trading_date.isoformat(),
        end_date=trading_date.isoformat(),
    )

    if schedule.empty:
        return None

    row = schedule.iloc[0]

    market_open = pd.Timestamp(row["market_open"]).to_pydatetime()
    market_close = pd.Timestamp(row["market_close"]).to_pydatetime()

    return TradingDaySchedule(
        trading_date=trading_date,
        market_open=market_open.astimezone(EASTERN),
        market_close=market_close.astimezone(EASTERN),
    )


def is_trading_day(value: datetime | date) -> bool:
    return trading_day_schedule(value) is not None


def market_session(
    value: datetime | None = None,
) -> MarketSession:
    current = _eastern_datetime(value)
    schedule = trading_day_schedule(current)

    if schedule is None:
        return MarketSession.CLOSED

    current_time = current.time()

    if current_time < time(4, 0):
        return MarketSession.OVERNIGHT

    if current_time < schedule.market_open.time():
        return MarketSession.PREMARKET

    if current < schedule.market_close:
        return MarketSession.CORE

    if current_time < time(20, 0):
        return MarketSession.AFTER_HOURS

    return MarketSession.OVERNIGHT