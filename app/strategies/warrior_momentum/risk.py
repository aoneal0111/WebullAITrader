"""Risk sizing adapter; callers must still pass every Atlas authorization result."""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from .configuration import RiskConfig
from .models import MomentumEntrySignal, PositionSize, ReasonCode


def size_position(
    signal: MomentumEntrySignal, *, account_equity: Decimal, buying_power: Decimal,
    allowed_symbols: frozenset[str], existing_exposure: Decimal = Decimal("0"),
    exposure_limit: Decimal | None = None, risk_engine_approved: bool = True,
    broker_restriction: bool = False, config: RiskConfig = RiskConfig(),
    symbol_authorized: bool | None = None,
    risk_rejection_reason: str | None = None,
    risk_context: dict[str, object] | None = None,
    diagnostic: Callable[[str, dict[str, object]], None] | None = None,
) -> PositionSize:
    return size_execution_position(
        symbol=signal.symbol, reference_price=signal.reference_price,
        risk_per_share=signal.risk_per_share, account_equity=account_equity,
        buying_power=buying_power, allowed_symbols=allowed_symbols,
        existing_exposure=existing_exposure, exposure_limit=exposure_limit,
        risk_engine_approved=risk_engine_approved,
        broker_restriction=broker_restriction, config=config,
        symbol_authorized=symbol_authorized,
        risk_rejection_reason=risk_rejection_reason,
        risk_context=risk_context, diagnostic=diagnostic,
    )


def size_execution_position(
    *, symbol: str, reference_price: Decimal, risk_per_share: Decimal,
    account_equity: Decimal, buying_power: Decimal,
    allowed_symbols: frozenset[str], existing_exposure: Decimal = Decimal("0"),
    exposure_limit: Decimal | None = None, risk_engine_approved: bool = True,
    broker_restriction: bool = False, config: RiskConfig = RiskConfig(),
    symbol_authorized: bool | None = None,
    risk_rejection_reason: str | None = None,
    risk_context: dict[str, object] | None = None,
    diagnostic: Callable[[str, dict[str, object]], None] | None = None,
) -> PositionSize:
    def emit(reason: str, **values: object) -> None:
        if diagnostic is None:
            return
        try:
            diagnostic(reason, values)
        except Exception:
            return

    # Reject unsuitable structure rather than tightening a stop inside market
    # noise. This gate also applies after adaptive widening and on re-entry.
    values = (risk_per_share, reference_price, account_equity, buying_power)
    if any(not value.is_finite() or value <= 0 for value in values):
        emit("RISK_REJECTED_INVALID_INPUT", reference_price=reference_price,
             risk_per_share=risk_per_share, account_equity=account_equity,
             buying_power=buying_power)
        return PositionSize(0, Decimal("0"), Decimal("0"), False, (ReasonCode.RISK_REJECTED,))
    if risk_per_share / reference_price * 100 > config.maximum_stop_distance_percent:
        emit("RISK_REJECTED_STOP_DISTANCE", reference_price=reference_price,
             risk_per_share=risk_per_share,
             stop_distance_percent=risk_per_share / reference_price * 100,
             maximum_stop_distance_percent=config.maximum_stop_distance_percent)
        return PositionSize(0, Decimal("0"), Decimal("0"), False, (ReasonCode.RISK_REJECTED,))
    risk_budget = min(config.configured_per_trade_risk, config.equity_risk_percentage * account_equity)
    raw = int((risk_budget / risk_per_share).to_integral_value(rounding=ROUND_FLOOR))
    affordable = int((buying_power / reference_price).to_integral_value(rounding=ROUND_FLOOR))
    equity_position_cap = account_equity * config.maximum_position_equity_percentage
    position_cap_dollars = min(config.maximum_position_dollars, equity_position_cap)
    position_cap = int((position_cap_dollars / reference_price).to_integral_value(rounding=ROUND_FLOOR))
    shares = max(0, min(raw, affordable, position_cap, config.maximum_quantity))
    reasons: list[ReasonCode] = []
    symbol_allowed = (
        symbol in allowed_symbols
        if symbol_authorized is None else symbol_authorized
    )
    if not symbol_allowed or broker_restriction:
        reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED)
        emit("RISK_REJECTED_BROKER_RESTRICTION" if broker_restriction else "RISK_REJECTED_SYMBOL_AUTHORIZATION",
             reference_price=reference_price, risk_per_share=risk_per_share,
             risk_budget=risk_budget, raw_risk_quantity=raw, affordable_quantity=affordable,
             position_cap_quantity=position_cap, maximum_quantity=config.maximum_quantity,
             maximum_position_dollars=config.maximum_position_dollars,
             selected_quantity=shares, buying_power=buying_power, account_equity=account_equity)
    if not risk_engine_approved or shares <= 0:
        reasons.append(ReasonCode.RISK_REJECTED)
        engine_reason = (
            "RISK_REJECTED_CAMPAIGN_LOSS"
            if risk_rejection_reason == "CAMPAIGN_LOSS"
            else "RISK_REJECTED_ENGINE"
            if not risk_engine_approved else "RISK_REJECTED_ZERO_SHARES"
        )
        emit(engine_reason,
             reference_price=reference_price, risk_per_share=risk_per_share,
             risk_budget=risk_budget, raw_risk_quantity=raw, affordable_quantity=affordable,
             position_cap_quantity=position_cap, maximum_quantity=config.maximum_quantity,
             maximum_position_dollars=config.maximum_position_dollars,
             selected_quantity=shares, buying_power=buying_power, account_equity=account_equity,
             **(risk_context or {}))
    position_dollars = reference_price * shares
    if exposure_limit is not None and existing_exposure + position_dollars > exposure_limit:
        reasons.append(ReasonCode.RISK_REJECTED)
        emit("RISK_REJECTED_EXPOSURE", reference_price=reference_price,
             risk_per_share=risk_per_share, risk_budget=risk_budget,
             raw_risk_quantity=raw, affordable_quantity=affordable,
             position_cap_quantity=position_cap, maximum_quantity=config.maximum_quantity,
             maximum_position_dollars=config.maximum_position_dollars,
             selected_quantity=shares, buying_power=buying_power,
             account_equity=account_equity, existing_exposure=existing_exposure,
             exposure_limit=exposure_limit)
    approved = not reasons
    return PositionSize(shares if approved else 0, risk_per_share * shares if approved else Decimal("0"),
                        position_dollars if approved else Decimal("0"), approved, tuple(dict.fromkeys(reasons)))


__all__ = ["size_execution_position", "size_position"]
