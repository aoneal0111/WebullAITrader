from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.paper_trading.order_models import (
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
)

ZERO = Decimal("0")


class MatchingError(ValueError):
    """Raised when an order or market quote cannot be matched safely."""


@dataclass(frozen=True, slots=True)
class MarketQuote:
    bid_price: Decimal
    ask_price: Decimal
    available_volume: Decimal
    timestamp: datetime
    last_trade_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.bid_price <= ZERO:
            raise MatchingError("bid_price must be positive")

        if self.ask_price <= ZERO:
            raise MatchingError("ask_price must be positive")

        if self.ask_price < self.bid_price:
            raise MatchingError("ask_price cannot be below bid_price")

        if self.available_volume < ZERO:
            raise MatchingError("available_volume cannot be negative")

        if self.timestamp.tzinfo is None:
            raise MatchingError("timestamp must be timezone-aware")

        if (
            self.last_trade_price is not None
            and self.last_trade_price <= ZERO
        ):
            raise MatchingError(
                "last_trade_price must be positive when provided"
            )


@dataclass(frozen=True, slots=True)
class MatchResult:
    order_id: str
    matched: bool
    filled_quantity: Decimal
    remaining_quantity: Decimal
    execution_price: Decimal | None
    slippage: Decimal
    timestamp: datetime
    liquidity_flag: str | None = None
    reason: str | None = None

    @property
    def is_partial(self) -> bool:
        return self.matched and self.remaining_quantity > ZERO


def match_order(
    order: PaperOrder,
    quote: MarketQuote,
) -> MatchResult:
    """Match one immutable order against one top-of-book quote."""

    if order.status not in {
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIALLY_FILLED,
    }:
        raise MatchingError(
            "only accepted or partially filled orders can be matched"
        )

    remaining = order.remaining_quantity

    if remaining <= ZERO:
        raise MatchingError("order has no remaining quantity")

    if quote.available_volume == ZERO:
        return _no_match(
            order,
            quote,
            reason="no available volume",
        )

    execution_price = _execution_price(order, quote)

    if execution_price is None:
        return _no_match(
            order,
            quote,
            reason="order price does not cross the market",
        )

    filled_quantity = min(
        remaining,
        quote.available_volume,
    )

    reference_price = _reference_price(order, quote)

    if order.request.side is OrderSide.BUY:
        slippage = execution_price - reference_price
    else:
        slippage = reference_price - execution_price

    return MatchResult(
        order_id=order.order_id,
        matched=True,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining - filled_quantity,
        execution_price=execution_price,
        slippage=slippage,
        timestamp=quote.timestamp,
        liquidity_flag="TAKER",
    )


def _execution_price(
    order: PaperOrder,
    quote: MarketQuote,
) -> Decimal | None:
    side = order.request.side
    order_type = order.request.order_type

    if side is OrderSide.BUY:
        market_price = quote.ask_price

        if order_type is OrderType.MARKET:
            return market_price

        if order_type is OrderType.LIMIT:
            limit_price = order.request.limit_price

            if limit_price is None:
                raise MatchingError(
                    "limit order requires limit_price"
                )

            if market_price <= limit_price:
                return market_price

            return None

    if side is OrderSide.SELL:
        market_price = quote.bid_price

        if order_type is OrderType.MARKET:
            return market_price

        if order_type is OrderType.LIMIT:
            limit_price = order.request.limit_price

            if limit_price is None:
                raise MatchingError(
                    "limit order requires limit_price"
                )

            if market_price >= limit_price:
                return market_price

            return None

    raise MatchingError(
        f"unsupported order type for matching: {order_type}"
    )


def _reference_price(
    order: PaperOrder,
    quote: MarketQuote,
) -> Decimal:
    if quote.last_trade_price is not None:
        return quote.last_trade_price

    if order.request.side is OrderSide.BUY:
        return quote.ask_price

    return quote.bid_price


def _no_match(
    order: PaperOrder,
    quote: MarketQuote,
    *,
    reason: str,
) -> MatchResult:
    return MatchResult(
        order_id=order.order_id,
        matched=False,
        filled_quantity=ZERO,
        remaining_quantity=order.remaining_quantity,
        execution_price=None,
        slippage=ZERO,
        timestamp=quote.timestamp,
        reason=reason,
    )