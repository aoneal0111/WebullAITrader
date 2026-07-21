from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import AssetClass
from app.paper_trading.matching_engine import (
    MarketQuote,
    MatchingError,
    match_order,
)
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderType,
    TimeInForce,
)
from app.paper_trading.orders import (
    accept_order,
    apply_fill,
    create_order,
)

D = Decimal
NOW = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)


def accepted_order(
    *,
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: Decimal = D("100"),
    limit_price: Decimal | None = None,
):
    request = OrderRequest(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=side,
        order_type=order_type,
        quantity=quantity,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
    )

    created = create_order(
        request,
        order_id_factory=lambda: "PAPER-1",
        clock=lambda: NOW,
    )

    return accept_order(
        created,
        at=NOW + timedelta(seconds=1),
    )


def quote(
    *,
    symbol: str = "AAPL",
    bid: str = "99",
    ask: str = "101",
    volume: str = "100",
    last: str | None = "100",
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        bid_price=D(bid),
        ask_price=D(ask),
        available_volume=D(volume),
        last_trade_price=None if last is None else D(last),
        timestamp=NOW + timedelta(seconds=2),
    )


def test_market_buy_fills_at_ask() -> None:
    result = match_order(accepted_order(), quote())

    assert result.matched is True
    assert result.filled_quantity == D("100")
    assert result.remaining_quantity == D("0")
    assert result.execution_price == D("101")
    assert result.slippage == D("1")
    assert result.is_partial is False


def test_market_sell_fills_at_bid() -> None:
    result = match_order(
        accepted_order(side=OrderSide.SELL),
        quote(),
    )

    assert result.execution_price == D("99")
    assert result.slippage == D("1")


def test_limit_buy_fills_when_ask_crosses_limit() -> None:
    result = match_order(
        accepted_order(
            order_type=OrderType.LIMIT,
            limit_price=D("101"),
        ),
        quote(ask="101"),
    )

    assert result.matched is True
    assert result.execution_price == D("101")


def test_limit_buy_does_not_fill_above_limit() -> None:
    result = match_order(
        accepted_order(
            order_type=OrderType.LIMIT,
            limit_price=D("100"),
        ),
        quote(ask="101"),
    )

    assert result.matched is False
    assert result.remaining_quantity == D("100")
    assert result.execution_price is None


def test_limit_sell_fills_when_bid_crosses_limit() -> None:
    result = match_order(
        accepted_order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=D("99"),
        ),
        quote(bid="99"),
    )

    assert result.matched is True
    assert result.execution_price == D("99")


def test_available_volume_creates_partial_fill() -> None:
    result = match_order(
        accepted_order(),
        quote(volume="40"),
    )

    assert result.filled_quantity == D("40")
    assert result.remaining_quantity == D("60")
    assert result.is_partial is True


def test_zero_volume_returns_no_match() -> None:
    result = match_order(
        accepted_order(),
        quote(volume="0"),
    )

    assert result.matched is False
    assert result.reason == "no available volume"


def test_partial_order_uses_remaining_quantity() -> None:
    order = apply_fill(
        accepted_order(),
        D("30"),
        D("101"),
        at=NOW + timedelta(seconds=2),
        fill_id_factory=lambda: "FILL-1",
    )

    result = match_order(
        order,
        MarketQuote(
            symbol="AAPL",
            bid_price=D("99"),
            ask_price=D("101"),
            available_volume=D("25"),
            last_trade_price=D("100"),
            timestamp=NOW + timedelta(seconds=3),
        ),
    )

    assert result.matched is True
    assert result.filled_quantity == D("25")
    assert result.remaining_quantity == D("45")
    assert result.is_partial is True


def test_crossed_quote_is_rejected() -> None:
    with pytest.raises(
        MatchingError,
        match="ask_price cannot be below bid_price",
    ):
        quote(bid="102", ask="101")

def test_match_order_rejects_symbol_mismatch() -> None:
    order = accepted_order(
        symbol="AAPL",
    )

    market_quote = quote(
        symbol="MSFT",
    )

    with pytest.raises(
        MatchingError,
        match="order and quote symbols must match",
    ):
        match_order(order, market_quote)

