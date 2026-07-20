from __future__ import annotations
from app.market_data.models import MarketEvent, MarketEventLog
from app.market_data.validation import validate_event


def append_event(log: MarketEventLog, event: MarketEvent) -> MarketEventLog:
    validate_event(event)
    if log.schema_version != 1: raise ValueError("unsupported market event schema")
    if any(item.source == event.source and item.sequence == event.sequence for item in log.events): raise ValueError("duplicate market event")
    if log.events:
        previous = log.events[-1]
        if event.sequence <= previous.sequence: raise ValueError("sequence must increase monotonically")
        if event.timestamp < previous.timestamp: raise ValueError("timestamp must not move backwards")
    return MarketEventLog((*log.events, event), log.schema_version)
