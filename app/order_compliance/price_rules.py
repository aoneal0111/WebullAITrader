from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from app.order_compliance.models import OrderType, ProposedOrder


@dataclass(frozen=True, slots=True)
class PriceValidation:
    failures: tuple[str, ...]
    normalized_limit_price: Decimal | None
    normalized_stop_price: Decimal | None
    lower_valid_tick: Decimal | None
    upper_valid_tick: Decimal | None


def validate_prices(order: ProposedOrder, tick_size: Decimal | None) -> PriceValidation:
    failures: list[str] = []
    normalized_limit: Decimal | None = None
    normalized_stop: Decimal | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None
    if order.order_type is OrderType.MARKET:
        if order.limit_price is not None or order.stop_price is not None:
            failures.append("MARKET orders must not contain limit or stop prices.")
        return PriceValidation(tuple(failures), None, None, None, None)
    if not _valid_decimal(tick_size):
        return PriceValidation(("Price tick size is missing, non-positive, or non-finite.",), None, None, None, None)
    required: list[tuple[str, Decimal | None]] = []
    if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
        required.append(("limit", order.limit_price))
    elif order.limit_price is not None:
        failures.append("Limit price is not allowed for this order type.")
    if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
        required.append(("stop", order.stop_price))
    elif order.stop_price is not None:
        failures.append("Stop price is not allowed for this order type.")
    for name, price in required:
        if price is None:
            failures.append(f"{name.title()} price is required.")
            continue
        if not _valid_decimal(price):
            failures.append(f"{name.title()} price must be a finite positive Decimal.")
            continue
        quotient = price / tick_size
        if quotient != quotient.to_integral_value():
            failures.append(f"{name.title()} price does not conform to tick size.")
            if lower is None:
                lower = quotient.to_integral_value(rounding=ROUND_FLOOR) * tick_size
                upper = lower + tick_size
            continue
        if name == "limit":
            normalized_limit = price
        else:
            normalized_stop = price
    return PriceValidation(tuple(failures), normalized_limit, normalized_stop, lower, upper)


def _valid_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
