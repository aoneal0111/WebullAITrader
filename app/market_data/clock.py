from __future__ import annotations
from datetime import datetime
from app.market_data.models import ClockMeasurement, ClockSyncPayload, HeartbeatPayload, MarketEvent, MarketEventType


def measure_clock(payload: ClockSyncPayload, received_timestamp: datetime) -> ClockMeasurement:
    _aware(received_timestamp)
    _aware(payload.exchange_timestamp); _aware(payload.local_timestamp)
    return ClockMeasurement(payload.exchange_timestamp, payload.local_timestamp,
                            _micros(received_timestamp - payload.exchange_timestamp),
                            _micros(payload.local_timestamp - payload.exchange_timestamp))


def heartbeat_is_stale(events: tuple[MarketEvent, ...], current_timestamp: datetime,
                       maximum_age_microseconds: int, connection_id: str | None = None) -> bool:
    _aware(current_timestamp)
    if not isinstance(maximum_age_microseconds, int) or isinstance(maximum_age_microseconds, bool) or maximum_age_microseconds < 0: raise ValueError("maximum heartbeat age is invalid")
    matches = tuple(item for item in events if item.event_type is MarketEventType.HEARTBEAT
                    and isinstance(item.payload, HeartbeatPayload)
                    and (connection_id is None or item.payload.connection_id == connection_id))
    return not matches or _micros(current_timestamp - matches[-1].timestamp) > maximum_age_microseconds


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None: raise ValueError("clock timestamps must be timezone-aware")
def _micros(value): return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
