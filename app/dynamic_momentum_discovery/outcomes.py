"""Forward labels kept strictly separate from discovery-time evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .models import DynamicMomentumObservation


HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class ForwardMarketPoint:
    timestamp: datetime
    price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("forward timestamp must be timezone-aware")
        if self.price <= 0:
            raise ValueError("forward price must be positive")


@dataclass(frozen=True, slots=True)
class DynamicMomentumOutcome:
    observation_id: str
    labeled_at: datetime
    return_5m_percent: Decimal | None
    return_15m_percent: Decimal | None
    return_30m_percent: Decimal | None
    maximum_favorable_excursion_percent: Decimal | None
    maximum_adverse_excursion_percent: Decimal | None
    new_high_continuation: bool | None
    breakout_failure: bool | None
    fade: bool | None
    spread_deterioration: bool | None
    research_only: bool = True
    execution_authorized: bool = False


def label_dynamic_momentum_outcome(
    observation: DynamicMomentumObservation,
    points: tuple[ForwardMarketPoint, ...],
    *,
    labeled_at: datetime,
) -> DynamicMomentumOutcome:
    cutoff = observation.snapshot.decision_cutoff
    if labeled_at.tzinfo is None or labeled_at.utcoffset() is None:
        raise ValueError("label time must be timezone-aware")
    if labeled_at <= cutoff:
        raise ValueError("label time must be after decision cutoff")
    if any(point.timestamp <= cutoff for point in points):
        raise ValueError("outcome points must be strictly after decision cutoff")
    if any(point.timestamp > labeled_at for point in points):
        raise ValueError("outcome points cannot exceed label time")
    ordered = tuple(sorted(points, key=lambda point: point.timestamp))
    base = observation.snapshot.price
    returns = tuple((point.price - base) / base * HUNDRED for point in ordered)
    initial_spread = observation.features.spread_percent
    final_spread = _spread_percent(ordered[-1]) if ordered else None
    return DynamicMomentumOutcome(
        observation_id=observation.observation_id,
        labeled_at=labeled_at,
        return_5m_percent=_horizon(ordered, cutoff, base, 5),
        return_15m_percent=_horizon(ordered, cutoff, base, 15),
        return_30m_percent=_horizon(ordered, cutoff, base, 30),
        maximum_favorable_excursion_percent=max(returns) if returns else None,
        maximum_adverse_excursion_percent=min(returns) if returns else None,
        new_high_continuation=(max(point.price for point in ordered) > (observation.snapshot.session_high or base) if ordered else None),
        breakout_failure=(min(point.price for point in ordered) < base and returns[-1] < 0 if ordered else None),
        fade=(returns[-1] < 0 if returns else None),
        spread_deterioration=(final_spread > initial_spread if final_spread is not None and initial_spread is not None else None),
    )


def _horizon(points, cutoff, base, minutes):
    target = cutoff + timedelta(minutes=minutes)
    point = next((item for item in points if item.timestamp >= target), None)
    return None if point is None else (point.price - base) / base * HUNDRED


def _spread_percent(point):
    if point.bid is None or point.ask in (None, Decimal("0")):
        return None
    return (point.ask - point.bid) / point.ask * HUNDRED


__all__ = [
    "DynamicMomentumOutcome", "ForwardMarketPoint",
    "label_dynamic_momentum_outcome",
]
