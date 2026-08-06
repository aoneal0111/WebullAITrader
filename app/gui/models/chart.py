from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ChartCandle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ChartViewSnapshot:
    symbol: str = "--"
    timeframe: str = "1D"
    market_status: str = "UNKNOWN"
    message: str = "Select a symbol to initialize the market chart."
    candles: tuple[ChartCandle, ...] = ()
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None
    volume: Decimal | None = None


__all__ = ["ChartCandle", "ChartViewSnapshot"]
