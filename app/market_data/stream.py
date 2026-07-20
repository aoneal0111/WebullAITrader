from __future__ import annotations
from app.market_data.events import append_event
from app.market_data.models import MarketEventLog


def collect_next(transport, log: MarketEventLog) -> MarketEventLog:
    event = transport.read_event()
    return log if event is None else append_event(log, event)


def collect_available(transport, log: MarketEventLog) -> MarketEventLog:
    while True:
        event = transport.read_event()
        if event is None: return log
        log = append_event(log, event)
