from __future__ import annotations
from app.market_data.models import MarketEvent, MarketEventType, MarketSession, SessionChangePayload


def recorded_session(event: MarketEvent) -> MarketSession:
    if event.event_type is not MarketEventType.SESSION_CHANGE or not isinstance(event.payload, SessionChangePayload):
        raise ValueError("an explicit session-change event is required")
    return event.payload.session


def latest_recorded_session(events: tuple[MarketEvent, ...], symbol: str | None = None) -> MarketSession | None:
    matching = tuple(item for item in events if item.event_type is MarketEventType.SESSION_CHANGE
                     and (symbol is None or item.symbol == symbol))
    return recorded_session(matching[-1]) if matching else None
