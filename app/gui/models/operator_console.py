from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum


class CandleInterval(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"

    @property
    def seconds(self) -> int:
        return {self.ONE_MINUTE: 60, self.FIVE_MINUTES: 300, self.FIFTEEN_MINUTES: 900}[self]


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
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("candle values must be finite Decimals")
        if self.volume < 0 or self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLCV values")


@dataclass(frozen=True, slots=True)
class CandleSeriesSnapshot:
    symbol: str | None
    interval: CandleInterval
    candles: tuple[Candle, ...] = ()
    venue: str | None = None


class CandleSeriesModel:
    """Deterministic, bounded candle aggregation for UI adapters."""

    def __init__(self, interval: CandleInterval = CandleInterval.ONE_MINUTE, *, max_candles: int = 500) -> None:
        if max_candles <= 0:
            raise ValueError("max_candles must be positive")
        self._interval = interval
        self._max_candles = max_candles
        self._symbol: str | None = None
        self._venue: str | None = None
        self._candles: dict[datetime, Candle] = {}

    @property
    def interval(self) -> CandleInterval:
        return self._interval

    def set_context(self, symbol: str | None, *, venue: str | None = None) -> None:
        self._symbol, self._venue = symbol, venue
        self._candles.clear()

    def set_interval(self, interval: CandleInterval) -> None:
        if not isinstance(interval, CandleInterval):
            raise TypeError("interval must be a CandleInterval")
        self._interval = interval
        self._candles.clear()

    def add_trade(self, timestamp: datetime, price: Decimal, volume: Decimal) -> CandleSeriesSnapshot:
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(price, Decimal) or price <= 0 or not price.is_finite():
            raise ValueError("price must be a positive finite Decimal")
        if not isinstance(volume, Decimal) or volume < 0 or not volume.is_finite():
            raise ValueError("volume must be a nonnegative finite Decimal")
        ts = timestamp.astimezone(timezone.utc)
        bucket = ts.replace(second=0, microsecond=0)
        epoch = int(bucket.timestamp())
        bucket = datetime.fromtimestamp(epoch - (epoch % self._interval.seconds), timezone.utc)
        current = self._candles.get(bucket)
        if current is None:
            self._candles[bucket] = Candle(bucket, price, price, price, price, volume)
        else:
            self._candles[bucket] = Candle(bucket, current.open, max(current.high, price), min(current.low, price), price, current.volume + volume)
        self._trim()
        return self.snapshot()

    def snapshot(self) -> CandleSeriesSnapshot:
        return CandleSeriesSnapshot(self._symbol, self._interval, tuple(self._candles[key] for key in sorted(self._candles)), self._venue)

    def _trim(self) -> None:
        for key in sorted(self._candles)[:-self._max_candles]:
            del self._candles[key]


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


def filter_markers(markers: tuple[ChartMarker, ...], symbol: str | None) -> tuple[ChartMarker, ...]:
    return tuple(marker for marker in markers if symbol is None or marker.symbol == symbol)
