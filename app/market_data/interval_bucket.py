from __future__ import annotations

from datetime import datetime

from app.market_data.candle_models import TimeFrame


def bucket_start(timestamp: datetime, interval: TimeFrame) -> datetime:
    """Return the inclusive start of the candle containing ``timestamp``."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if not isinstance(interval, TimeFrame):
        raise ValueError("interval must be TimeFrame")

    if interval is TimeFrame.ONE_DAY:
        return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = int((timestamp - midnight).total_seconds())
    interval_seconds = int(interval.duration.total_seconds())
    bucket_seconds = elapsed_seconds - (elapsed_seconds % interval_seconds)
    return midnight + interval.duration * (bucket_seconds // interval_seconds)


def bucket_end(timestamp: datetime, interval: TimeFrame) -> datetime:
    """Return the exclusive end of the candle containing ``timestamp``."""
    return bucket_start(timestamp, interval) + interval.duration
