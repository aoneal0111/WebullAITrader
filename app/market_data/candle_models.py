from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class TimeFrame(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"

    @property
    def duration(self) -> timedelta:
        return {
            TimeFrame.ONE_MINUTE: timedelta(minutes=1),
            TimeFrame.FIVE_MINUTES: timedelta(minutes=5),
            TimeFrame.FIFTEEN_MINUTES: timedelta(minutes=15),
            TimeFrame.THIRTY_MINUTES: timedelta(minutes=30),
            TimeFrame.ONE_HOUR: timedelta(hours=1),
            TimeFrame.ONE_DAY: timedelta(days=1),
        }[self]


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _require_decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return value


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: TimeFrame
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int = 0

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)

        if not isinstance(self.interval, TimeFrame):
            raise ValueError("interval must be TimeFrame")

        _require_aware(self.open_time, "open_time")
        _require_aware(self.close_time, "close_time")
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        if self.close_time - self.open_time != self.interval.duration:
            raise ValueError("candle duration must match interval")

        for name in ("open", "high", "low", "close"):
            _require_decimal(getattr(self, name), name, positive=True)
        _require_decimal(self.volume, "volume")

        if self.high < max(self.open, self.close):
            raise ValueError("high cannot be below open or close")
        if self.low > min(self.open, self.close):
            raise ValueError("low cannot be above open or close")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        if isinstance(self.trade_count, bool) or not isinstance(self.trade_count, int):
            raise ValueError("trade_count must be an integer")
        if self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")


@dataclass(frozen=True, slots=True)
class CandleSeries:
    symbol: str
    interval: TimeFrame
    candles: tuple[Candle, ...] = ()

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)

        if not isinstance(self.interval, TimeFrame):
            raise ValueError("interval must be TimeFrame")
        if not isinstance(self.candles, tuple):
            raise ValueError("candles must be an immutable tuple")

        previous_open_time: datetime | None = None
        for candle in self.candles:
            if not isinstance(candle, Candle):
                raise ValueError("candles must contain Candle values")
            if candle.symbol != symbol:
                raise ValueError("candle symbol must match series symbol")
            if candle.interval is not self.interval:
                raise ValueError("candle interval must match series interval")
            if previous_open_time is not None and candle.open_time <= previous_open_time:
                raise ValueError("candles must be strictly ordered by open_time")
            previous_open_time = candle.open_time
