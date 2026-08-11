"""Risk sizing adapter; callers must still pass every Atlas authorization result."""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from .configuration import RiskConfig
from .models import MomentumEntrySignal, PositionSize, ReasonCode


def size_position(
    signal: MomentumEntrySignal, *, account_equity: Decimal, buying_power: Decimal,
    allowed_symbols: frozenset[str], existing_exposure: Decimal = Decimal("0"),
    exposure_limit: Decimal | None = None, risk_engine_approved: bool = True,
    broker_restriction: bool = False, config: RiskConfig = RiskConfig(),
) -> PositionSize:
    risk_budget = min(config.configured_per_trade_risk, config.equity_risk_percentage * account_equity)
    raw = int((risk_budget / signal.risk_per_share).to_integral_value(rounding=ROUND_FLOOR))
    affordable = int((buying_power / signal.reference_price).to_integral_value(rounding=ROUND_FLOOR))
    position_cap = int((config.maximum_position_dollars / signal.reference_price).to_integral_value(rounding=ROUND_FLOOR))
    shares = max(0, min(raw, affordable, position_cap, config.maximum_quantity))
    reasons: list[ReasonCode] = []
    if signal.symbol not in allowed_symbols or broker_restriction:
        reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED)
    if not risk_engine_approved or shares <= 0:
        reasons.append(ReasonCode.RISK_REJECTED)
    position_dollars = signal.reference_price * shares
    if exposure_limit is not None and existing_exposure + position_dollars > exposure_limit:
        reasons.append(ReasonCode.RISK_REJECTED)
    approved = not reasons
    return PositionSize(shares if approved else 0, signal.risk_per_share * shares if approved else Decimal("0"),
                        position_dollars if approved else Decimal("0"), approved, tuple(dict.fromkeys(reasons)))


__all__ = ["size_position"]
