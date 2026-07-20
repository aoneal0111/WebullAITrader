from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from decimal import Decimal

from app.indicators.market_snapshot import MarketSnapshot


class VolatilityRegime(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class VolatilitySignal:
    score: Decimal
    regime: VolatilityRegime
    atr_percent: Decimal | None
    reason: str


def score_volatility(snapshot: MarketSnapshot) -> VolatilitySignal:
    """Return a 0..1 risk-quality score; high volatility receives a lower score."""
    if snapshot.atr_14 is None or snapshot.close <= 0:
        return VolatilitySignal(Decimal("0.5"), VolatilityRegime.UNKNOWN, None, "ATR is unavailable.")
    atr_percent = snapshot.atr_14 / snapshot.close * Decimal(100)
    if atr_percent < Decimal(1):
        regime, score = VolatilityRegime.LOW, Decimal("0.8")
    elif atr_percent <= Decimal(3):
        regime, score = VolatilityRegime.NORMAL, Decimal(1)
    else:
        regime, score = VolatilityRegime.HIGH, max(Decimal(0), Decimal(1) - (atr_percent - Decimal(3)) / Decimal(7))
    return VolatilitySignal(
        score, regime, atr_percent, f"ATR is {atr_percent:.2f}% of price ({regime.value.lower()} volatility)."
    )
