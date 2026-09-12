"""Bounded point-in-time research features; outcome labels are intentionally absent."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from statistics import median
from typing import Iterable

from .models import HistoricalBar

FEATURE_SCHEMA_VERSION = 1
FEATURE_DERIVATION_VERSION = "ATLAS_PIT_FEATURES_V1"
FEATURE_SNAPSHOT_KEYS = frozenset(("schema_version", "derivation_version", "as_of_timestamp", "values", "quality_flags", "provenance"))


def _pct(value: Decimal, base: Decimal | None) -> str | None:
    return None if base in (None, 0) else str(value / base * 100)


def _slope(values: list[Decimal]) -> str | None:
    if len(values) < 2:
        return None
    return str((values[-1] - values[0]) / Decimal(len(values) - 1))


def derive_point_in_time_features(bars: Iterable[HistoricalBar], decision_timestamp, *,
                                  previous_close: Decimal | None = None,
                                  trigger_price: Decimal | None = None,
                                  structural_stop: Decimal | None = None,
                                  memberships: Iterable[str] = (),
                                  maturation: dict[str, object] | None = None) -> dict[str, object]:
    """Derive only from bars whose one-minute interval completed by decision time."""
    eligible = tuple(sorted((bar for bar in bars if bar.timestamp + timedelta(minutes=1) <= decision_timestamp),
                            key=lambda bar: bar.timestamp))
    if not eligible:
        raise ValueError("INSUFFICIENT_HISTORY")
    last = eligible[-1]
    closes = [bar.close for bar in eligible]
    ranges = [bar.high - bar.low for bar in eligible]
    returns = {f"return_{n}bar": _pct(last.close, closes[-n-1]) if len(closes) > n else None
               for n in (1, 2, 3, 5, 10)}
    body = abs(last.close - last.open)
    full_range = last.high - last.low
    green = sum(1 for bar in eligible[-10:] if bar.close > bar.open)
    red = sum(1 for bar in eligible[-10:] if bar.close < bar.open)
    premarket = tuple(bar for bar in eligible if bar.session.upper() == "PREMARKET")
    regular = tuple(bar for bar in eligible if bar.session.upper() == "REGULAR")
    session_high = max((bar.high for bar in regular), default=None)
    session_low = min((bar.low for bar in regular), default=None)
    vwap_numerator = sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in regular)
    vwap_denominator = sum((bar.volume for bar in regular), Decimal("0"))
    vwap = vwap_numerator / vwap_denominator if vwap_denominator else None
    volume_window = [bar.volume for bar in eligible[-10:]]
    dollar_volume = last.close * last.volume
    regular_open = regular[0].timestamp if regular else None
    minutes_from_open = None if regular_open is None else int((last.timestamp - regular_open).total_seconds() // 60)
    if not regular:
        time_bucket = "PREMARKET" if premarket else "AFTER_HOURS"
    elif minutes_from_open < 5:
        time_bucket = "OPEN_0_5"
    elif minutes_from_open < 15:
        time_bucket = "OPEN_5_15"
    elif minutes_from_open < 30:
        time_bucket = "OPEN_15_30"
    elif minutes_from_open < 60:
        time_bucket = "OPEN_30_60"
    elif minutes_from_open < 120:
        time_bucket = "MID_MORNING"
    elif minutes_from_open < 240:
        time_bucket = "MIDDAY"
    elif minutes_from_open < 360:
        time_bucket = "AFTERNOON"
    else:
        time_bucket = "POWER_HOUR"
    membership_ids = tuple(sorted(set(memberships)))
    recent_low = min((bar.low for bar in eligible[-10:]), default=None)
    extension = {
        "price_at_detection": str(last.close),
        "trigger_price": None if trigger_price is None else str(trigger_price),
        "distance_from_trigger_percent": None if trigger_price is None else _pct(last.close - trigger_price, trigger_price),
        "distance_from_structural_stop_percent": None if structural_stop is None else _pct(last.close - structural_stop, structural_stop),
        "distance_from_hod_percent": _pct(last.close - session_high, session_high) if session_high else None,
        "distance_from_lod_percent": _pct(last.close - session_low, session_low) if session_low else None,
        "distance_from_vwap_percent": _pct(last.close - vwap, vwap) if vwap else None,
        "distance_from_previous_close_percent": _pct(last.close - previous_close, previous_close) if previous_close else None,
        "distance_from_premarket_high_percent": _pct(last.close - max((bar.high for bar in premarket), default=None), max((bar.high for bar in premarket), default=None)) if premarket else None,
        "distance_from_opening_range_high_percent": None,
        "extension_from_recent_low_percent": _pct(last.close - recent_low, recent_low) if recent_low else None,
        "extension_from_impulse_start_percent": None,
    }
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_derivation_version": FEATURE_DERIVATION_VERSION,
        "decision_timestamp": decision_timestamp.isoformat(),
        "time_of_day_bucket": time_bucket,
        "minutes_from_regular_open": minutes_from_open,
        "strategy_combination": "+".join(membership_ids) if membership_ids else None,
        "membership_count": len(membership_ids),
        "bars_used": len(eligible), "last_bar_timestamp": last.timestamp.isoformat(),
        "candle": {"body": str(body), "body_percent": _pct(body, last.open), "range": str(full_range),
                    "range_percent": _pct(full_range, last.open), "upper_wick": str(last.high - max(last.open, last.close)),
                    "lower_wick": str(min(last.open, last.close) - last.low),
                    "body_to_range": None if not full_range else str(body / full_range),
                    "close_location": None if not full_range else str((last.close - last.low) / full_range),
                    "classification": "BULLISH" if last.close > last.open else "BEARISH" if last.close < last.open else "DOJI"},
        "recent_structure": {"green_bars_10": green, "red_bars_10": red,
                             "higher_highs_10": sum(eligible[i].high > eligible[i-1].high for i in range(max(1, len(eligible)-9), len(eligible))),
                             "higher_lows_10": sum(eligible[i].low > eligible[i-1].low for i in range(max(1, len(eligible)-9), len(eligible)))},
        "momentum": {**returns, "slope_5bar": _slope(closes[-5:]), "slope_10bar": _slope(closes[-10:])},
        "volume": {"current": str(last.volume), "rolling_3_mean": str(sum((bar.volume for bar in eligible[-3:]), Decimal("0")) / min(3, len(eligible))),
                   "rolling_5_median": str(median([bar.volume for bar in eligible[-5:]])),
                   "rolling_10_mean": str(sum(volume_window, Decimal("0")) / len(volume_window)),
                   "dollar_volume": str(dollar_volume), "trade_count": last.trade_count,
                   "provider_vwap": None if last.provider_vwap is None else str(last.provider_vwap),
                   "provider_volume_quality": "SINGLE_EXCHANGE_FREE_RESEARCH"},
        "levels": {"previous_close": None if previous_close is None else str(previous_close),
                   "distance_from_previous_close_percent": _pct(last.close - previous_close, previous_close) if previous_close else None,
                   "session_hod_as_of_decision": None if session_high is None else str(session_high),
                   "session_lod_as_of_decision": None if session_low is None else str(session_low),
                   "distance_from_hod_percent": _pct(last.close - session_high, session_high) if session_high else None,
                   "vwap": None if vwap is None else str(vwap),
                   "distance_to_vwap_percent": _pct(last.close - vwap, vwap) if vwap else None},
        "premarket": {"bar_count": len(premarket), "high": None if not premarket else str(max(bar.high for bar in premarket)),
                      "low": None if not premarket else str(min(bar.low for bar in premarket)),
                      "volume": str(sum((bar.volume for bar in premarket), Decimal("0")))},
        "opening": {"minutes_from_regular_open": None if not regular else int((last.timestamp - regular[0].timestamp).total_seconds() // 60),
                    "bar_count": len(regular), "range_high_5": None if len(regular) < 5 else str(max(bar.high for bar in regular[:5])),
                    "range_low_5": None if len(regular) < 5 else str(min(bar.low for bar in regular[:5]))},
        "volatility": {"median_range_10": str(median(ranges[-10:])), "current_range": str(full_range),
                       "range_vs_median_10": str(full_range / median(ranges[-10:])) if median(ranges[-10:]) else None},
        "maturation": maturation or {"bars_since_impulse_start": None, "seconds_since_impulse_start": None,
                                      "impulse_duration_bars": None, "impulse_magnitude_percent": None,
                                      "pullback_duration_bars": None, "pullback_depth_percent": None,
                                      "consolidation_duration_bars": None, "consolidation_range_percent": None,
                                      "bars_since_first_resistance_test": None, "bars_since_reclaim": None,
                                      "number_resistance_tests": None, "number_support_tests": None},
        "extension": extension,
        "data_quality": {"providers": sorted({bar.provider for bar in eligible}), "feeds": sorted({bar.feed for bar in eligible}),
                        "premarket_available": bool(premarket), "quote_data_available": any(bar.bid is not None for bar in eligible),
                        "benchmark_context_available": False, "fundamental_context_available": False,
                        "outcome_fields_present": False},
    }


def feature_snapshot(bars: Iterable[HistoricalBar], decision_timestamp, *,
                     previous_close: Decimal | None = None, trigger_price: Decimal | None = None,
                     structural_stop: Decimal | None = None, memberships: Iterable[str] = (),
                     provider: str | None = None, feed: str | None = None,
                     repository_commit: str | None = None, normalization_version: int | None = None,
                     coverage_class: str | None = None,
                     maturation: dict[str, object] | None = None) -> dict[str, object]:
    values = derive_point_in_time_features(
        bars, decision_timestamp, previous_close=previous_close, trigger_price=trigger_price,
        structural_stop=structural_stop, memberships=memberships, maturation=maturation,
    )
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "derivation_version": FEATURE_DERIVATION_VERSION,
        "as_of_timestamp": decision_timestamp.isoformat(),
        "values": values,
        "quality_flags": values.get("data_quality", {}),
        "provenance": {"provider": provider, "feed": feed, "coverage_class": coverage_class,
                        "feature_derivation_version": FEATURE_DERIVATION_VERSION,
                        "repository_commit": repository_commit,
                        "normalization_version": normalization_version},
    }


def prefix_invariant_features(prefix: Iterable[HistoricalBar], suffix: Iterable[HistoricalBar], decision_timestamp,
                              *, previous_close: Decimal | None = None) -> bool:
    left = derive_point_in_time_features(prefix, decision_timestamp, previous_close=previous_close)
    combined = derive_point_in_time_features(tuple(prefix) + tuple(suffix), decision_timestamp, previous_close=previous_close)
    return left == combined
