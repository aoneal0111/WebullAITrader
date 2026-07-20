from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN

from app.risk.limits import DEFAULT_RISK_LIMITS, RiskLimits


def calculate_risk_score(confidence: int, current_price: Decimal, atr: Decimal | None) -> int:
    """Return risk severity from 0 (lower) to 100 (higher)."""
    confidence_risk = Decimal(100 - confidence) * Decimal("0.6")
    if atr is None or not atr.is_finite() or atr < 0 or current_price <= 0:
        volatility_risk = Decimal(25)
    else:
        volatility_risk = min(Decimal(40), atr / current_price * Decimal(100) * Decimal(8))
    return max(0, min(100, int((confidence_risk + volatility_risk).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))))


def calculate_max_position_percent(
    *, approved: bool, confidence: int, current_price: Decimal, atr: Decimal | None,
    limits: RiskLimits = DEFAULT_RISK_LIMITS,
) -> Decimal:
    """Return an advisory allocation percentage, never shares or order quantity."""
    if not approved:
        return Decimal(0)
    confidence_factor = max(Decimal(0), min(Decimal(1), Decimal(confidence) / Decimal(100)))
    if atr is None or not atr.is_finite() or atr < 0:
        return min(limits.missing_atr_position_percent, limits.maximum_position_percent * confidence_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    atr_percent = atr / current_price * Decimal(100)
    volatility_factor = max(Decimal("0.2"), min(Decimal(1), Decimal(3) / max(atr_percent, Decimal("0.01"))))
    return min(limits.maximum_position_percent, limits.maximum_position_percent * confidence_factor * volatility_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
