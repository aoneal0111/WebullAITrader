from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from decimal import Decimal, ROUND_HALF_EVEN

from app.indicators.market_snapshot import MarketSnapshot
from app.strategy.momentum import score_momentum
from app.strategy.trend import score_trend
from app.strategy.volatility import VolatilityRegime, score_volatility


class StrategyAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class StrategyScore:
    action: StrategyAction
    confidence: int
    score: Decimal
    trend_score: Decimal
    momentum_score: Decimal
    volatility_score: Decimal
    volatility_regime: VolatilityRegime
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketAnalysis:
    trend: str
    momentum: str
    volatility: str
    ema_cross: bool
    macd_cross: bool
    rsi_state: str
    bollinger_state: str
    overall_score: int

    def to_dict(self) -> dict[str, str | bool | int]:
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "ema_cross": self.ema_cross,
            "macd_cross": self.macd_cross,
            "rsi_state": self.rsi_state,
            "bollinger_state": self.bollinger_state,
            "overall_score": self.overall_score,
        }


def score_snapshot(snapshot: MarketSnapshot) -> StrategyScore:
    """Combine deterministic indicators into an advisory, non-executable signal."""
    trend = score_trend(snapshot)
    momentum = score_momentum(snapshot)
    volatility = score_volatility(snapshot)
    directional = trend.score * Decimal("0.65") + momentum.score * Decimal("0.35")
    combined = max(Decimal(-1), min(Decimal(1), directional * volatility.score))
    action = (
        StrategyAction.BUY
        if combined >= Decimal("0.35")
        else StrategyAction.SELL
        if combined <= Decimal("-0.35")
        else StrategyAction.HOLD
    )
    raw_confidence = abs(combined) * Decimal(100) if action is not StrategyAction.HOLD else (Decimal(1) - abs(combined)) * Decimal(100)
    confidence = int(raw_confidence.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return StrategyScore(
        action=action,
        confidence=max(0, min(100, confidence)),
        score=combined,
        trend_score=trend.score,
        momentum_score=momentum.score,
        volatility_score=volatility.score,
        volatility_regime=volatility.regime,
        reasons=(trend.reason, momentum.reason, volatility.reason),
    )


def analyze_snapshot(snapshot: MarketSnapshot) -> MarketAnalysis:
    """Return a compact, JSON-ready technical-analysis summary."""
    score = score_snapshot(snapshot)
    return MarketAnalysis(
        trend=_trend_label(score.trend_score),
        momentum=_momentum_label(snapshot),
        volatility=score.volatility_regime.value.title(),
        ema_cross=snapshot.ema_12 > snapshot.ema_26,
        macd_cross=snapshot.macd > snapshot.macd_signal,
        rsi_state=_rsi_state(snapshot.rsi_14),
        bollinger_state=_bollinger_state(snapshot),
        overall_score=max(0, min(100, int(((score.score + Decimal(1)) * Decimal(50)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)))),
    )


def _trend_label(value: Decimal) -> str:
    return "Bullish" if value > 0 else "Bearish" if value < 0 else "Neutral"


def _momentum_label(snapshot: MarketSnapshot) -> str:
    if snapshot.rsi_14 is None:
        return "Unknown"
    if snapshot.macd_histogram > 0 and snapshot.rsi_14 >= 50:
        return "Strengthening"
    if snapshot.macd_histogram < 0 and snapshot.rsi_14 <= 50:
        return "Weakening"
    return "Mixed"


def _rsi_state(value: Decimal | None) -> str:
    if value is None:
        return "Unknown"
    return "Overbought" if value >= 70 else "Oversold" if value <= 30 else "Neutral"


def _bollinger_state(snapshot: MarketSnapshot) -> str:
    if snapshot.bollinger_lower is None or snapshot.bollinger_upper is None:
        return "Unknown"
    if snapshot.close <= snapshot.bollinger_lower:
        return "Lower Band Touch"
    if snapshot.close >= snapshot.bollinger_upper:
        return "Upper Band Touch"
    return "Inside Bands"
