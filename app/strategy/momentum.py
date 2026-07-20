from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.indicators.market_snapshot import MarketSnapshot


@dataclass(frozen=True, slots=True)
class MomentumSignal:
    score: Decimal
    reason: str


def score_momentum(snapshot: MarketSnapshot) -> MomentumSignal:
    """Score RSI momentum from -1 to 1 while penalizing extremes."""
    value = snapshot.rsi_14
    if value is None:
        return MomentumSignal(Decimal(0), "RSI is unavailable; momentum is neutral.")
    if value >= Decimal(70):
        score = -min(Decimal(1), (value - Decimal(70)) / Decimal(15))
        label = "overbought"
    elif value <= Decimal(30):
        score = min(Decimal(1), (Decimal(30) - value) / Decimal(15))
        label = "oversold"
    else:
        score = (value - Decimal(50)) / Decimal(20)
        label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return MomentumSignal(score, f"RSI {value:.2f} indicates {label} momentum.")
