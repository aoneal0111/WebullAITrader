"""Timestamp-aligned one-minute feature construction."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .models import FeatureSnapshot, MinuteBar

HUNDRED = Decimal("100")
BAR_INTERVAL = timedelta(minutes=1)


def aligned_bars(bars: tuple[MinuteBar, ...]) -> tuple[MinuteBar, ...]:
    if not bars:
        return ()
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    symbol = ordered[0].symbol.strip().upper()
    if any(bar.symbol.strip().upper() != symbol for bar in ordered):
        raise ValueError("feature bars must contain one symbol")
    if len({bar.timestamp for bar in ordered}) != len(ordered):
        raise ValueError("duplicate bar timestamps are not allowed")
    return ordered


def contiguous_tail(
    bars: tuple[MinuteBar, ...],
    *,
    interval: timedelta = BAR_INTERVAL,
) -> tuple[MinuteBar, ...]:
    """Return the newest uninterrupted sequence of one-minute bars.

    Setup geometry must never bridge missing candles. Historical preload and
    live capture may legitimately contain a gap; only the contiguous tail is
    eligible for mechanical setup detection.
    """
    ordered = aligned_bars(bars)
    if not ordered:
        return ()

    start = len(ordered) - 1
    while start > 0:
        if ordered[start].timestamp - ordered[start - 1].timestamp != interval:
            break
        start -= 1

    return ordered[start:]


def completed_bars_as_of(
    bars: tuple[MinuteBar, ...], as_of: datetime,
) -> tuple[MinuteBar, ...]:
    """Return only bars completed by ``as_of`` (timestamps denote bar opens)."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return tuple(
        bar for bar in aligned_bars(bars)
        if bar.timestamp + BAR_INTERVAL <= as_of
    )


def current_completed_bar_tail(
    bars: tuple[MinuteBar, ...],
    as_of: datetime,
    *,
    interval: timedelta = BAR_INTERVAL,
) -> tuple[MinuteBar, ...]:
    """Return a contiguous tail ending in the immediately prior minute.

    Bar timestamps denote opens.  The newest bar supplied to a current live
    setup evaluation must therefore end exactly at the evaluation minute's
    boundary.  This comparison deliberately does not require both timestamps
    to share a scanner session, so valid PREMARKET/REGULAR/AFTER_HOURS boundary
    evidence remains eligible.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    completed = completed_bars_as_of(bars, as_of)
    tail = contiguous_tail(completed, interval=interval)
    if not tail:
        return ()
    evaluation_minute = as_of.replace(second=0, microsecond=0)
    if tail[-1].timestamp + interval != evaluation_minute:
        return ()
    return tail


def rolling_change(bars: tuple[MinuteBar, ...], minutes: int) -> Decimal | None:
    ordered = aligned_bars(bars)
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if len(ordered) < minutes + 1:
        return None
    end = ordered[-1]
    threshold = end.timestamp.timestamp() - minutes * 60
    eligible = tuple(bar for bar in ordered[:-1] if bar.timestamp.timestamp() <= threshold)
    if not eligible:
        return None
    start = eligible[-1].close
    return (end.close - start) / start * HUNDRED


def build_features(bars: tuple[MinuteBar, ...], *, lookback: int = 10) -> FeatureSnapshot | None:
    ordered = aligned_bars(bars)
    if not ordered:
        return None
    window = ordered[-max(2, lookback):]
    latest = ordered[-1]
    session_high = max(bar.high for bar in ordered)
    session_low = min(bar.low for bar in ordered)
    rolling_high = max(bar.high for bar in window)
    rolling_low = min(bar.low for bar in window)
    base = window[0].close
    change = (latest.close - base) / base * HUNDRED
    rolling_volume = sum((bar.volume for bar in window), Decimal("0"))
    previous = window[:-1]
    average_previous = (
        sum((bar.volume for bar in previous), Decimal("0")) / len(previous)
        if previous else None
    )
    acceleration = (
        latest.volume / average_previous
        if average_previous is not None and average_previous > 0 else None
    )
    total_volume = sum((bar.volume for bar in ordered), Decimal("0"))
    vwap = None
    if total_volume > 0:
        vwap = sum(
            (((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume for bar in ordered),
            Decimal("0"),
        ) / total_volume
    distance_vwap = None if vwap is None else (latest.close - vwap) / vwap * HUNDRED
    distance_hod = (session_high - latest.close) / session_high * HUNDRED
    recent_peak = max(bar.high for bar in window)
    pullback = (recent_peak - latest.close) / recent_peak * HUNDRED
    consolidation = _consolidation_duration(window)
    resistance = max((bar.high for bar in ordered[:-1]), default=None)
    breakout_ratio = (
        latest.volume / average_previous
        if average_previous is not None and average_previous > 0 else None
    )
    return FeatureSnapshot(
        symbol=latest.symbol.strip().upper(), timestamp=latest.timestamp,
        session_high=session_high, session_low=session_low,
        rolling_high=rolling_high, rolling_low=rolling_low,
        rolling_change_percent=change, rolling_volume=rolling_volume,
        volume_acceleration=acceleration, vwap=vwap,
        distance_from_vwap_percent=distance_vwap,
        distance_from_hod_percent=distance_hod,
        pullback_depth_percent=pullback,
        consolidation_duration=consolidation,
        breakout_level=resistance, breakout_volume_ratio=breakout_ratio,
    )


def _consolidation_duration(bars: tuple[MinuteBar, ...]) -> int:
    if len(bars) < 2:
        return 0
    duration = 0
    anchor = bars[-1].close
    for bar in reversed(bars[:-1]):
        if abs(anchor - bar.close) / anchor * HUNDRED <= Decimal("2"):
            duration += 1
        else:
            break
    return duration


__all__ = [
    "aligned_bars",
    "contiguous_tail",
    "completed_bars_as_of",
    "current_completed_bar_tail",
    "rolling_change",
    "build_features",
]
