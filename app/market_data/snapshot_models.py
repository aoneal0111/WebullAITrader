from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .candle_models import Candle, TimeFrame


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class CandleSeriesSnapshot:
    """Immutable view of a candle series at a specific publication sequence."""

    symbol: str
    interval: TimeFrame
    sequence: int
    created_at: datetime
    completed: tuple[Candle, ...] = ()
    current: Candle | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)

        if not isinstance(self.interval, TimeFrame):
            raise ValueError("interval must be TimeFrame")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ValueError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")

        _require_aware(self.created_at, "created_at")

        if not isinstance(self.completed, tuple):
            raise ValueError("completed must be an immutable tuple")

        previous_open_time: datetime | None = None
        for candle in self.completed:
            self._validate_candle(candle, "completed")
            if previous_open_time is not None and candle.open_time <= previous_open_time:
                raise ValueError("completed candles must be strictly ordered by open_time")
            previous_open_time = candle.open_time

        if self.current is not None:
            self._validate_candle(self.current, "current")
            if previous_open_time is not None and self.current.open_time <= previous_open_time:
                raise ValueError("current candle must follow completed candles")

    def _validate_candle(self, candle: Candle, field_name: str) -> None:
        if not isinstance(candle, Candle):
            raise ValueError(f"{field_name} must contain Candle values")
        if candle.symbol != self.symbol:
            raise ValueError(f"{field_name} candle symbol must match snapshot symbol")
        if candle.interval is not self.interval:
            raise ValueError(f"{field_name} candle interval must match snapshot interval")

    @property
    def candle_count(self) -> int:
        """Return the total number of completed and current candles."""
        return len(self.completed) + (1 if self.current is not None else 0)
