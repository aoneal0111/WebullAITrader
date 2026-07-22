from __future__ import annotations

from decimal import Decimal

from app.ai.response_models import AIResponse, ResponseAction
from app.indicators.market_snapshot import MarketSnapshot
from app.risk.limits import DEFAULT_RISK_LIMITS, RiskLimits
from app.risk.models import LegacyRiskDecision
from app.risk.sizing import calculate_max_position_percent, calculate_risk_score


def evaluate_risk(
    response: AIResponse, snapshot: MarketSnapshot, limits: RiskLimits = DEFAULT_RISK_LIMITS
) -> LegacyRiskDecision:
    """Validate an advisory response using only supplied, in-memory data."""
    if not isinstance(response, AIResponse) or not isinstance(snapshot, MarketSnapshot):
        return _rejected("Malformed risk input.", 100, ("Expected AIResponse and MarketSnapshot.",))

    if response.action is ResponseAction.HOLD:
        warnings = () if _valid_price(snapshot.close) else ("Current price is malformed or unavailable.",)
        return LegacyRiskDecision(True, "HOLD requires no market exposure.", 0, Decimal(0), True, True, warnings)

    if response.action not in (ResponseAction.BUY, ResponseAction.SELL):
        return _rejected("Unsupported action.", 100, ("Only BUY, SELL, or HOLD is allowed.",))
    if not isinstance(response.confidence, int) or isinstance(response.confidence, bool):
        return _rejected("Malformed confidence.", 100, ("Confidence must be an integer.",))
    if not 0 <= response.confidence <= 100:
        return _rejected("Malformed confidence.", 100, ("Confidence must be between 0 and 100.",))
    if not _valid_price(snapshot.close):
        return _rejected("Invalid current price.", 100, ("Current price must be finite and positive.",))

    current = Decimal(str(snapshot.close))
    stop = response.stop_loss
    target = response.take_profit
    stop_valid = _valid_decimal_price(stop)
    target_valid = _valid_decimal_price(target)
    warnings: list[str] = []

    if response.confidence < limits.minimum_confidence:
        warnings.append(f"Confidence is below the {limits.minimum_confidence}% minimum.")
    if stop is None:
        warnings.append("Stop-loss is required.")
    elif not stop_valid:
        warnings.append("Stop-loss must be finite and positive.")
    if target is None:
        warnings.append("Take-profit is required.")
    elif not target_valid:
        warnings.append("Take-profit must be finite and positive.")

    if stop_valid and stop is not None:
        stop_valid = stop < current if response.action is ResponseAction.BUY else stop > current
        if not stop_valid:
            warnings.append(
                "BUY stop-loss must be below current price."
                if response.action is ResponseAction.BUY
                else "SELL stop-loss must be above current price."
            )
    if target_valid and target is not None:
        target_valid = target > current if response.action is ResponseAction.BUY else target < current
        if not target_valid:
            warnings.append(
                "BUY take-profit must be above current price."
                if response.action is ResponseAction.BUY
                else "SELL take-profit must be below current price."
            )

    ratio: Decimal | None = None
    if stop_valid and target_valid and stop is not None and target is not None:
        risk = abs(current - stop)
        reward = abs(target - current)
        if risk > 0:
            ratio = reward / risk
        if ratio is None or ratio < limits.minimum_reward_risk_ratio:
            warnings.append(
                f"Reward:risk ratio must be at least {limits.minimum_reward_risk_ratio}:1."
            )

    approved = not warnings
    atr = snapshot.atr_14 if isinstance(snapshot.atr_14, Decimal) else None
    risk_score = calculate_risk_score(response.confidence, snapshot.close, atr)
    max_position = calculate_max_position_percent(
        approved=approved,
        confidence=response.confidence,
        current_price=snapshot.close,
        atr=atr,
        limits=limits,
    )
    if atr is None:
        warnings.append("ATR is unavailable; conservative sizing applies.")
    reason = (
        f"Approved: confidence and price levels pass the {limits.minimum_reward_risk_ratio}:1 reward:risk minimum."
        if approved
        else "Rejected: one or more deterministic risk limits failed."
    )
    return LegacyRiskDecision(
        approved, reason, risk_score, max_position, stop_valid, target_valid, tuple(warnings)
    )


def _valid_price(value: object) -> bool:
    return (
        isinstance(value, (int, float, Decimal))
        and not isinstance(value, bool)
        and Decimal(str(value)).is_finite()
        and Decimal(str(value)) > 0
    )


def _valid_decimal_price(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _rejected(reason: str, risk_score: int, warnings: tuple[str, ...]) -> LegacyRiskDecision:
    return LegacyRiskDecision(False, reason, risk_score, Decimal(0), False, False, warnings)
