"""Future labels are constructed separately and cannot enter evaluation."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from .contracts import AdaptiveEntryRecommendation, OutcomeObservation


def label_outcome(recommendation: AdaptiveEntryRecommendation, *, observed_at: datetime,
                  future_price: Decimal, high: Decimal | None = None,
                  low: Decimal | None = None,
                  horizon_seconds: int | None = None) -> OutcomeObservation:
    elapsed = int((observed_at - recommendation.decision_cutoff).total_seconds())
    horizon = elapsed if horizon_seconds is None else horizon_seconds
    if horizon <= 0 or elapsed < horizon:
        raise ValueError("outcome horizon must follow the decision cutoff")
    original = recommendation.original.entry
    fresh = recommendation.fresh_hypothetical.entry
    mfe = None if original is None else (high or future_price) - original
    mae = None if original is None else (low or future_price) - original
    original_fill = None if original is None or low is None else low <= original
    fresh_fill = None if fresh is None or low is None else low <= fresh
    identity = sha256(f"{recommendation.recommendation_id}|{horizon}|{observed_at.isoformat()}".encode()).hexdigest()
    return OutcomeObservation(identity, recommendation.recommendation_id,
                              recommendation.decision_cutoff, horizon, observed_at,
                              future_price, mfe, mae, original_fill, fresh_fill,
                              "RESEARCH_OBSERVED_PRICE_TOUCH_V1")


@dataclass(slots=True)
class _Tracked:
    recommendation: AdaptiveEntryRecommendation
    prices: deque[tuple[datetime, Decimal, Decimal | None, Decimal | None]]
    emitted: set[int] = field(default_factory=set)


class BoundedOutcomeTracker:
    """Bound recommendation and price history while emitting fixed horizons."""

    def __init__(self, *, horizons: tuple[int, ...] = (5, 15, 30, 60, 300),
                 maximum_recommendations: int = 2048,
                 maximum_points_per_recommendation: int = 512) -> None:
        if not horizons or any(value <= 0 for value in horizons):
            raise ValueError("positive outcome horizons are required")
        if maximum_recommendations <= 0 or maximum_points_per_recommendation <= 0:
            raise ValueError("outcome bounds must be positive")
        self.horizons = tuple(sorted(set(horizons)))
        self.maximum_recommendations = maximum_recommendations
        self.maximum_points_per_recommendation = maximum_points_per_recommendation
        self._tracked: OrderedDict[str, _Tracked] = OrderedDict()

    def track(self, recommendation: AdaptiveEntryRecommendation) -> None:
        self._tracked[recommendation.recommendation_id] = _Tracked(
            recommendation, deque(maxlen=self.maximum_points_per_recommendation),
        )
        self._tracked.move_to_end(recommendation.recommendation_id)
        while len(self._tracked) > self.maximum_recommendations:
            self._tracked.popitem(last=False)

    def observe(self, *, symbol: str, observed_at: datetime, price: Decimal,
                high: Decimal | None = None, low: Decimal | None = None) -> tuple[OutcomeObservation, ...]:
        labels: list[OutcomeObservation] = []
        for tracked in tuple(self._tracked.values()):
            recommendation = tracked.recommendation
            if recommendation.symbol != symbol.strip().upper() or observed_at <= recommendation.decision_cutoff:
                continue
            tracked.prices.append((observed_at, price, high, low))
            elapsed = int((observed_at - recommendation.decision_cutoff).total_seconds())
            highs = tuple(item[2] or item[1] for item in tracked.prices)
            lows = tuple(item[3] or item[1] for item in tracked.prices)
            for horizon in self.horizons:
                if horizon <= elapsed and horizon not in tracked.emitted:
                    labels.append(label_outcome(
                        recommendation, observed_at=observed_at, future_price=price,
                        high=max(highs), low=min(lows), horizon_seconds=horizon,
                    ))
                    tracked.emitted.add(horizon)
        return tuple(labels)

    @property
    def retained_recommendations(self) -> int:
        return len(self._tracked)

    @property
    def maximum_retained_points(self) -> int:
        return max((len(item.prices) for item in self._tracked.values()), default=0)


__all__ = ["BoundedOutcomeTracker", "label_outcome"]
