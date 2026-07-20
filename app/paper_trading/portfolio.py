from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.order_compliance.models import OrderSide
from app.paper_trading.models import PaperPortfolio, PaperPosition

ZERO = Decimal("0")


def create_portfolio(initial_cash: Decimal, timestamp: datetime) -> PaperPortfolio:
    if not _nonnegative(initial_cash) or timestamp.tzinfo is None:
        raise ValueError("initial cash and timestamp are invalid")
    return PaperPortfolio(initial_cash, initial_cash, (), ZERO, ZERO, initial_cash, timestamp)


def apply_fill(
    portfolio: PaperPortfolio,
    symbol: str,
    side: OrderSide,
    quantity: Decimal,
    fill_price: Decimal,
    mark_price: Decimal,
    timestamp: datetime,
) -> tuple[PaperPortfolio, Decimal]:
    if not all(_positive(value) for value in (quantity, fill_price, mark_price)) or timestamp.tzinfo is None:
        raise ValueError("fill values are malformed")
    positions = {position.symbol: position for position in portfolio.positions}
    key = symbol.strip().upper()
    existing = positions.get(key)
    realized = ZERO
    notional = quantity * fill_price
    if side is OrderSide.BUY:
        if portfolio.cash < notional:
            raise ValueError("insufficient simulated cash")
        old_quantity = existing.quantity if existing else ZERO
        old_cost = existing.average_cost if existing else ZERO
        new_quantity = old_quantity + quantity
        average_cost = (old_quantity * old_cost + notional) / new_quantity
        cash = portfolio.cash - notional
    elif side is OrderSide.SELL:
        if existing is None or quantity > existing.quantity:
            raise ValueError("sale exceeds simulated long position")
        new_quantity = existing.quantity - quantity
        average_cost = existing.average_cost
        realized = (fill_price - existing.average_cost) * quantity
        cash = portfolio.cash + notional
    else:
        raise ValueError("unsupported side")
    if new_quantity == ZERO:
        positions.pop(key, None)
    else:
        positions[key] = _position(key, new_quantity, average_cost, mark_price)
    ordered = tuple(sorted(positions.values(), key=lambda item: item.symbol))
    unrealized = sum((position.unrealized_pnl for position in ordered), ZERO)
    equity = cash + sum((position.market_value for position in ordered), ZERO)
    return PaperPortfolio(
        portfolio.initial_cash, cash, ordered, portfolio.realized_pnl + realized,
        unrealized, equity, timestamp,
    ), realized


def _position(symbol: str, quantity: Decimal, average_cost: Decimal, mark: Decimal) -> PaperPosition:
    market_value = quantity * mark
    return PaperPosition(symbol, quantity, average_cost, mark, market_value, (mark - average_cost) * quantity)


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > ZERO


def _nonnegative(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= ZERO
