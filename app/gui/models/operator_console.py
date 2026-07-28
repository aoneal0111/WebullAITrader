from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class CandleInterval(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        values = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in values
        ):
            raise ValueError("candle values must be finite Decimals")
        if (
            self.volume < 0
            or self.high < max(self.open, self.close)
            or self.low > min(self.open, self.close)
        ):
            raise ValueError("invalid OHLCV values")


@dataclass(frozen=True, slots=True)
class CandleSeriesSnapshot:
    symbol: str | None
    interval: CandleInterval
    candles: tuple[Candle, ...] = ()
    venue: str | None = None


class ChartMarkerKind(StrEnum):
    BUY_FILL = "BUY_FILL"
    SELL_FILL = "SELL_FILL"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    POSITION_CLOSED = "POSITION_CLOSED"
    STOP_UPDATE = "STOP_UPDATE"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    DECISION = "DECISION"


@dataclass(frozen=True, slots=True)
class ChartMarker:
    occurred_at: datetime
    symbol: str
    kind: ChartMarkerKind
    quantity: str | None = None
    price: str | None = None
    order_id: str | None = None
    strategy: str | None = None
    confidence: str | None = None
    reason: str | None = None
    realized_pnl: str | None = None

