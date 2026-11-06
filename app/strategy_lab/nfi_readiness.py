"""Observable NFI USD activity checks; no orders or strategy overrides."""
from datetime import UTC, datetime
from math import isfinite


def primary_activity(rows, now):
    """Count absent/zero-volume intervals against NFI's existing 288/60 rule."""
    completed = {}
    for row in rows:
        if len(row) < 6 or not all(isfinite(float(v)) for v in row[:6]):
            return dict(eligible=False, reason='INVALID_OHLCV')
        ts = int(row[0])
        if ts % 300000 or row[5] < 0 or min(row[1:5]) <= 0:
            return dict(eligible=False, reason='INVALID_OHLCV')
        if ts / 1000 + 300 <= now:
            if ts in completed:
                return dict(eligible=False, reason='DUPLICATE_CANDLE')
            completed[ts] = row
    if not completed:
        return dict(eligible=False, reason='NO_COMPLETED_CANDLES')
    latest = max(completed)
    age = now - latest / 1000 - 300
    span = (latest - min(completed)) // 300000 + 1
    nonempty = sum(r[5] > 0 for ts, r in completed.items() if latest - 287 * 300000 <= ts <= latest)
    empty = 288 - nonempty
    reason = ('STALE_PRIMARY' if age > 600 else 'INSUFFICIENT_WARMUP' if span < 710
              else 'SOURCE_EMPTY_CANDLE_LIMIT' if empty > 60 else 'SOURCE_ACTIVITY_OK')
    return dict(eligible=reason == 'SOURCE_ACTIVITY_OK', reason=reason,
                zero_volume_or_absent_288=empty, completed_span=span, last_closed_age_seconds=age)


def source_snapshot(frame):
    """Explain analyzed source flags. Eligibility is not a BUY recommendation."""
    if frame.empty:
        return dict(state='NO_ANALYZED_CANDLE')
    row = frame.iloc[-1]
    import pandas as pd
    def value(name):
        v = row.get(name)
        return None if v is None or pd.isna(v) else v
    buy = value('enter_long')
    empty = value('num_empty_288')
    protection = value('protections_long_global')
    key = ('RSI_3', 'RSI_3_15m', 'RSI_3_1h', 'RSI_3_4h', 'RSI_3_1d')
    missing = [k for k in key if value(k) is None]
    reason = ('SOURCE_BUY_SIGNAL' if buy == 1 else 'SOURCE_SIGNAL_UNAVAILABLE' if buy is None
              else 'SOURCE_KEY_INDICATOR_NOT_READY' if missing
              else 'SOURCE_EMPTY_CANDLE_LIMIT' if empty is not None and empty > 60
              else 'SOURCE_GLOBAL_PROTECTION' if protection is not None and not protection
              else 'NO_SOURCE_BUY_SIGNAL')
    return dict(state=reason, candle_at=row['date'].isoformat(),
                buy=None if buy is None else int(buy),
                enter_tag=str(row.get('enter_tag', '')), missing_key_indicators=missing,
                zero_volume_288=None if empty is None else float(empty),
                global_protection=None if protection is None else bool(protection))


def runtime_freshness(report, now=None):
    """Keep an old RUNNING checkpoint from appearing to be a live process."""
    now = now or datetime.now(UTC)
    status = report.get('engine-status.json') or {}
    final = report.get('capture-final.json') or {}
    updated = status.get('updated_at')
    try:
        age = (now - datetime.fromisoformat(updated)).total_seconds()
    except (TypeError, ValueError):
        return dict(state='STATUS_UNAVAILABLE', process_liveness_verified=False)
    journal = final.get('journal') or {}
    # The closed journal is authoritative only when it belongs to this worker's
    # current capture. A previous run's final file must not mark a restart stopped.
    same_capture = (final.get('recording_id') == status.get('recording_id')
                    if final.get('recording_id') and status.get('recording_id') else True)
    try:
        final_newer = datetime.fromisoformat(journal.get('updated_at')) >= datetime.fromisoformat(updated)
    except (TypeError, ValueError):
        final_newer = False
    closed = bool(same_capture and final_newer and journal.get('closed'))
    state = 'STOPPED' if closed else 'STALE_STATUS' if age < -5 or age > 120 else 'RECENT_CHECKPOINT'
    return dict(state=state, checkpoint_age_seconds=round(age, 1),
                process_liveness_verified=False,
                note='A checkpoint is not an operating-system process check.')
