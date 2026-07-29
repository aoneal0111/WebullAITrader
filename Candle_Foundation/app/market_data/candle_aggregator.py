from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.market_data.candle_models import Candle, TimeFrame
from app.market_data.interval_bucket import bucket_start
from app.market_data.models import MarketEvent, MarketEventType, TradePayload


class CandleAggregator:
    """Build one symbol/timeframe candle stream from canonical trade events."""

    def __init__(self, interval: TimeFrame, symbol: str | None = None) -> None:
        if not isinstance(interval, TimeFrame):
            raise ValueError("interval must be TimeFrame")
        normalized_symbol = None
        if symbol is not None:
            normalized_symbol = symbol.strip().upper()
            if not normalized_symbol:
                raise ValueError("symbol must be non-empty when provided")

        self._interval = interval
        self._symbol = normalized_symbol
        self._current: Candle | None = None
        self._last_timestamp: datetime | None = None

    @property
    def interval(self) -> TimeFrame:
        return self._interval

    @property
    def symbol(self) -> str | None:
        return self._symbol

    @property
    def current_candle(self) -> Candle | None:
        return self._current

    def on_event(self, event: MarketEvent) -> Candle | None:
        """Consume a trade event and return a completed candle on rollover."""
        symbol, timestamp, price, size = self._trade_values(event)

        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ValueError("trade events must not move backward in time")
        self._last_timestamp = timestamp

        if self._symbol is None:
            self._symbol = symbol
        elif symbol != self._symbol:
            raise ValueError("trade symbol does not match aggregator symbol")

        open_time = bucket_start(timestamp, self._interval)
        if self._current is None:
            self._current = self._new_candle(symbol, open_time, price, size)
            return None

        if open_time < self._current.open_time:
            raise ValueError("trade belongs to an already completed candle bucket")

        if open_time == self._current.open_time:
            self._current = replace(
                self._current,
                high=max(self._current.high, price),
                low=min(self._current.low, price),
                close=price,
                volume=self._current.volume + size,
                trade_count=self._current.trade_count + 1,
            )
            return None

        completed = self._current
        self._current = self._new_candle(symbol, open_time, price, size)
        return completed

    def flush(self) -> Candle | None:
        """Return and clear the in-progress candle."""
        completed = self._current
        self._current = None
        self._last_timestamp = None
        return completed

    def _new_candle(
        self,
        symbol: str,
        open_time: datetime,
        price: Decimal,
        size: Decimal,
    ) -> Candle:
        return Candle(
            symbol=symbol,
            interval=self._interval,
            open_time=open_time,
            close_time=open_time + self._interval.duration,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=size,
            trade_count=1,
        )

    @staticmethod
    def _trade_values(
        event: MarketEvent,
    ) -> tuple[str, datetime, Decimal, Decimal]:
        if not isinstance(event, MarketEvent):
            raise ValueError("event must be MarketEvent")
        if event.event_type is not MarketEventType.TRADE:
            raise ValueError("candle aggregation requires TRADE events")
        if not isinstance(event.payload, TradePayload):
            raise ValueError("TRADE event payload must be TradePayload")
        if event.symbol is None or not event.symbol.strip():
            raise ValueError("trade event symbol is required")
        if event.timestamp.tzinfo is None or event.timestamp.utcoffset() is None:
            raise ValueError("trade event timestamp must be timezone-aware")

        price = event.payload.price
        size = event.payload.size
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise ValueError("trade price must be a positive finite Decimal")
        if not isinstance(size, Decimal) or not size.is_finite() or size < 0:
            raise ValueError("trade size must be a non-negative finite Decimal")

        return event.symbol.strip().upper(), event.timestamp, price, size
