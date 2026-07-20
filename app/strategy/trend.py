from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.indicators.market_snapshot import MarketSnapshot


@dataclass(frozen=True, slots=True)
class TrendSignal:
    score: Decimal
    reason: str


def score_trend(snapshot: MarketSnapshot) -> TrendSignal:
    """Score trend confirmation from -1 (bearish) to 1 (bullish)."""
    votes = [
        _direction(snapshot.close, snapshot.ema_12),
        _direction(snapshot.ema_12, snapshot.ema_26),
        _direction(snapshot.macd, snapshot.macd_signal),
        _direction(snapshot.macd_histogram, Decimal(0)),
    ]
    score = Decimal(sum(votes)) / Decimal(len(votes))
    label = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
    return TrendSignal(score, f"Trend indicators are {label} ({sum(votes):+d}/4 confirmations).")


def _direction(left: Decimal, right: Decimal) -> int:
    return 1 if left > right else -1 if left < right else 0
