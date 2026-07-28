from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.market_data import MarketEvent


@runtime_checkable
class HistoricalMarketFeed(Protocol):
    def events(self) -> tuple[MarketEvent, ...]: ...

    @property
    def start_time(self) -> datetime: ...

    @property
    def end_time(self) -> datetime: ...

    @property
    def event_count(self) -> int: ...


class InMemoryHistoricalMarketFeed:
    def __init__(self, values: tuple[MarketEvent, ...] = ()) -> None:
        if not isinstance(values, tuple):
            raise TypeError("values must be an immutable tuple")
        if any(not isinstance(value, MarketEvent) for value in values):
            raise TypeError("values must contain only MarketEvent instances")
        self._events = tuple(
            value
            for _, value in sorted(
                enumerate(values),
                key=lambda item: (
                    item[1].timestamp,
                    item[0],
                ),
            )
        )

    def events(self) -> tuple[MarketEvent, ...]:
        return self._events

    @property
    def start_time(self) -> datetime:
        if not self._events:
            raise ValueError("empty feed has no start_time")
        return self._events[0].timestamp

    @property
    def end_time(self) -> datetime:
        if not self._events:
            raise ValueError("empty feed has no end_time")
        return self._events[-1].timestamp

    @property
    def event_count(self) -> int:
        return len(self._events)
