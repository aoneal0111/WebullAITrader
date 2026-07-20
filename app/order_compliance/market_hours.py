from __future__ import annotations

from datetime import datetime, timedelta

from app.order_compliance.models import (
    ComplianceLimits,
    MarketComplianceState,
    MarketStatus,
    OrderType,
    ProposedOrder,
    SymbolStatus,
    TradingSession,
)


def validate_market_and_session(
    order: ProposedOrder,
    market: MarketComplianceState,
    limits: ComplianceLimits,
    now: datetime,
) -> tuple[str, ...]:
    failures: list[str] = []
    timestamps = (
        market.regular_session_open, market.regular_session_close,
        market.extended_session_open, market.extended_session_close, market.status_as_of, now,
    )
    if any(not isinstance(value, datetime) or value.tzinfo is None for value in timestamps):
        return ("Market/session timestamps must be timezone-aware.",)
    age = now - market.status_as_of
    if age < timedelta(0) or age > timedelta(seconds=limits.maximum_market_status_age_seconds):
        failures.append("Market status is stale or future-dated.")
    if market.symbol_status is not SymbolStatus.TRADABLE:
        failures.append(f"Symbol status is not tradable: {getattr(market.symbol_status, 'value', 'UNKNOWN')}.")
    if market.market_status is not MarketStatus.OPEN:
        failures.append(f"Market status does not permit trading: {getattr(market.market_status, 'value', 'UNKNOWN')}.")
    if order.requested_session is TradingSession.REGULAR:
        if not market.regular_session_open <= now <= market.regular_session_close:
            failures.append("Current time is outside the supplied regular session.")
    elif order.requested_session is TradingSession.EXTENDED_HOURS:
        if not limits.allow_extended_hours:
            failures.append("Extended-hours trading is disabled.")
        if not market.extended_session_open <= now <= market.extended_session_close:
            failures.append("Current time is outside the supplied extended-hours session.")
        if order.order_type is OrderType.MARKET and not limits.allow_market_orders_in_extended_hours:
            failures.append("Market orders are disabled during extended hours.")
    else:
        failures.append("Trading session is unknown.")
    return tuple(failures)
