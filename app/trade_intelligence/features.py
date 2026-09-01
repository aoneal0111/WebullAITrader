"""Point-in-time deterministic feature extraction from completed minute bars."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .models import PriceBar

ONE_MINUTE = timedelta(minutes=1)
HUNDRED = Decimal("100")


def extract_completed_bar_features(
    bars: tuple[PriceBar, ...], *, decision_cutoff,
) -> tuple[
    tuple[tuple[str, Decimal | int | bool | str | None], ...],
    tuple[tuple[str, object], ...],
]:
    """Return only features whose authoritative bars completed by the cutoff."""

    ordered = tuple(sorted(bars, key=lambda item: item.timestamp))
    if any(bar.timestamp + ONE_MINUTE > decision_cutoff for bar in ordered):
        raise ValueError("anti-lookahead violation: incomplete/future bar supplied")
    if not ordered:
        return _unavailable_features(), ()
    latest = ordered[-1]
    session_high = max(item.high for item in ordered)
    distance_hod = (latest.close - session_high) / session_high * HUNDRED
    changes = tuple(item.close - item.open for item in ordered)
    consecutive_red = _consecutive(changes, lambda value: value < 0)
    consecutive_green = _consecutive(changes, lambda value: value > 0)
    pullback = _trailing(ordered, lambda bar: bar.close < bar.open)
    pullback_high = max((item.high for item in pullback), default=None)
    pullback_low = min((item.low for item in pullback), default=None)
    pullback_depth = (
        None if pullback_high is None or pullback_low is None
        else (pullback_high - pullback_low) / pullback_high * HUNDRED
    )
    ranges = tuple(item.high - item.low for item in ordered[-6:])
    compression = None
    if len(ranges) >= 6:
        older = sum(ranges[:3]) / Decimal(3)
        newer = sum(ranges[3:]) / Decimal(3)
        compression = None if older == 0 else newer / older
    volume_acceleration = None
    if len(ordered) >= 2 and ordered[-2].volume > 0:
        volume_acceleration = latest.volume / ordered[-2].volume
    pullback_contraction = None
    if len(pullback) >= 2 and pullback[0].volume > 0:
        pullback_contraction = pullback[-1].volume / pullback[0].volume
    higher_low = None if len(ordered) < 2 else ordered[-1].low > ordered[-2].low
    momentum_velocity = None
    if len(ordered) >= 2:
        first = ordered[max(0, len(ordered) - 6)]
        minutes = Decimal(len(ordered) - max(0, len(ordered) - 6))
        momentum_velocity = (latest.close - first.close) / first.close * HUNDRED / minutes
    features = (
        ("distance_from_hod_percent", distance_hod),
        ("pullback_depth_percent", pullback_depth),
        ("pullback_bars", len(pullback)),
        ("consecutive_red_bars", consecutive_red),
        ("consecutive_green_bars", consecutive_green),
        ("higher_low", higher_low),
        ("consolidation_duration_bars", _consolidation_duration(ordered)),
        ("range_compression_ratio", compression),
        ("volume_acceleration_ratio", volume_acceleration),
        ("pullback_volume_contraction_ratio", pullback_contraction),
        ("recent_realized_range_percent", (max(item.high for item in ordered[-5:]) - min(item.low for item in ordered[-5:])) / latest.close * HUNDRED),
        ("recent_momentum_velocity_percent_per_minute", momentum_velocity),
        # Authoritative VWAP and initial-expansion anchors are not derivable
        # from this generic boundary and remain explicitly unavailable.
        ("distance_from_vwap_percent", None),
        ("breakout_distance_percent", None),
        ("breakout_volume_expansion_ratio", None),
        ("time_since_initial_momentum_expansion_seconds", None),
    )
    sources = tuple((name, latest.timestamp + ONE_MINUTE) for name, value in features if value is not None)
    return features, sources


def _unavailable_features():
    return tuple((name, None) for name in (
        "distance_from_hod_percent", "pullback_depth_percent", "pullback_bars",
        "consecutive_red_bars", "consecutive_green_bars", "higher_low",
        "consolidation_duration_bars", "range_compression_ratio",
        "volume_acceleration_ratio", "pullback_volume_contraction_ratio",
        "recent_realized_range_percent", "recent_momentum_velocity_percent_per_minute",
        "distance_from_vwap_percent", "breakout_distance_percent",
        "breakout_volume_expansion_ratio", "time_since_initial_momentum_expansion_seconds",
    ))


def _consecutive(values, predicate) -> int:
    count = 0
    for value in reversed(values):
        if not predicate(value):
            break
        count += 1
    return count


def _trailing(values, predicate):
    result = []
    for value in reversed(values):
        if not predicate(value):
            break
        result.append(value)
    return tuple(reversed(result))


def _consolidation_duration(bars: tuple[PriceBar, ...]) -> int:
    if len(bars) < 2:
        return 1
    peak = max(item.high for item in bars)
    floor = peak * Decimal("0.97")
    count = 0
    for bar in reversed(bars):
        if bar.low < floor:
            break
        count += 1
    return count
