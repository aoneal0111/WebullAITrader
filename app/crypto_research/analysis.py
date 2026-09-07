"""Transparent bounded feature, event, and score calculations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import pstdev
from typing import Sequence

from .models import CryptoFeatures, CryptoMomentumEventType, CryptoObservation


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def calculate_features(
    history: Sequence[CryptoObservation],
    *,
    cutoff: datetime,
) -> CryptoFeatures:
    if not history:
        raise ValueError("crypto history is required")
    current = history[-1]
    age = Decimal(str(max(0.0, (cutoff - current.timestamp).total_seconds())))
    prices = tuple(item.price for item in history)
    volumes = tuple(item.volume for item in history)
    change = _change(prices[-1], prices[0]) if len(prices) > 1 else None
    velocity = change / Decimal(len(prices) - 1) if change is not None else None
    acceleration = None
    if len(prices) >= 3:
        acceleration = _change(prices[-1], prices[-2]) - _change(
            prices[-2], prices[-3]
        )
    volume_acceleration = None
    if len(volumes) >= 3:
        previous_volume_change = volumes[-2] - volumes[-3]
        current_volume_change = volumes[-1] - volumes[-2]
        if previous_volume_change > ZERO and current_volume_change >= ZERO:
            volume_acceleration = current_volume_change / previous_volume_change
    spread = (
        current.ask - current.bid
        if current.bid is not None and current.ask is not None
        else None
    )
    spread_percent = (
        spread / current.price * HUNDRED if spread is not None else None
    )
    returns = tuple(
        _change(prices[index], prices[index - 1])
        for index in range(1, len(prices))
        if prices[index - 1] > ZERO
    )
    volatility = (
        Decimal(str(pstdev(float(item) for item in returns)))
        if len(returns) >= 2
        else None
    )
    prior_high = max(
        (item.high or item.price for item in history[:-1]), default=None
    )
    breakout = (
        _change(current.price, prior_high) if prior_high is not None else None
    )
    ranges = tuple(
        (item.high - item.low)
        for item in history
        if item.high is not None and item.low is not None
    )
    range_expansion = (
        ranges[-1] / ranges[-2]
        if len(ranges) >= 2 and ranges[-2] > ZERO
        else None
    )
    return CryptoFeatures(
        percentage_change=change,
        short_window_acceleration=acceleration,
        volume=current.volume,
        notional_volume=current.price * current.volume,
        volume_acceleration=volume_acceleration,
        # A time-of-week baseline requires multiple prior weeks; unavailable
        # evidence remains explicit rather than borrowing an equity baseline.
        relative_volume_time_of_week=None,
        spread=spread,
        spread_percent=spread_percent,
        quote_freshness_seconds=age,
        volatility=volatility,
        high_breakout_distance_percent=breakout,
        range_expansion=range_expansion,
        trend_velocity=velocity,
    )


def detect_events(
    history: Sequence[CryptoObservation], features: CryptoFeatures
) -> tuple[CryptoMomentumEventType, ...]:
    events: list[CryptoMomentumEventType] = []
    breakout = features.high_breakout_distance_percent
    if breakout is not None and breakout > Decimal("0.10"):
        events.append(CryptoMomentumEventType.RANGE_BREAKOUT)
    if breakout is not None and breakout > ZERO:
        events.append(CryptoMomentumEventType.HIGH_BREAK)
    if (
        features.short_window_acceleration is not None
        and features.short_window_acceleration > Decimal("0.05")
    ):
        events.append(CryptoMomentumEventType.MOMENTUM_ACCELERATION)
    if (
        features.volume_acceleration is not None
        and features.volume_acceleration >= Decimal("1.5")
    ):
        events.append(CryptoMomentumEventType.VOLUME_EXPANSION)
    if features.range_expansion is not None and features.range_expansion >= Decimal("1.5"):
        events.append(CryptoMomentumEventType.VOLATILITY_EXPANSION)
    if len(history) >= 3:
        if history[-3].price < history[-2].price and history[-1].price > history[-2].price:
            events.append(CryptoMomentumEventType.PULLBACK_CONTINUATION)
        if history[-3].price > history[-2].price and history[-1].price > history[-2].price:
            events.append(CryptoMomentumEventType.REVERSAL_ATTEMPT)
    return tuple(events)


def score_features(
    features: CryptoFeatures,
    events: Sequence[CryptoMomentumEventType],
) -> tuple[Decimal, tuple[tuple[str, Decimal], ...]]:
    acceleration = _cap(_positive(features.short_window_acceleration) * 50, 25)
    volume = _cap((_positive(features.volume_acceleration) - 1) * 10, 20)
    liquidity = _cap(features.notional_volume / Decimal("1000000"), 15)
    spread_quality = (
        ZERO
        if features.spread_percent is None
        else _cap(Decimal("15") - features.spread_percent * 15, 15)
    )
    breakout = _cap(_positive(features.high_breakout_distance_percent) * 5, 15)
    trend = _cap(_positive(features.trend_velocity) * 10, 10)
    structure = _cap(Decimal(len(events)) * Decimal("2.5"), 15)
    components = (
        ("price_acceleration", acceleration),
        ("volume_acceleration", volume),
        ("liquidity", liquidity),
        ("spread_quality", spread_quality),
        ("breakout_structure", breakout),
        ("trend_persistence", trend),
        ("event_structure", structure),
    )
    return min(HUNDRED, sum((value for _, value in components), ZERO)), components


def _change(current: Decimal, previous: Decimal) -> Decimal:
    return ZERO if previous == ZERO else (current - previous) / previous * HUNDRED


def _positive(value: Decimal | None) -> Decimal:
    return ZERO if value is None else max(ZERO, value)


def _cap(value: Decimal, maximum: int) -> Decimal:
    return min(Decimal(maximum), max(ZERO, value))


__all__ = ["calculate_features", "detect_events", "score_features"]
