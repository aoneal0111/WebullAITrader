from datetime import UTC, datetime
from decimal import Decimal

from app.momentum_scanner import AssetClass
from app.paper_trading.order_book_api import (
    OrderBookOrderRequest,
    OrderBookOrderSide,
    OrderBookOrderType,
    OrderBookTimeInForce,
    create_order,
)

NOW = datetime(2026, 7, 21, 14, 0, tzinfo=UTC)


def make_order(
    order_id: str,
    *,
    symbol: str = "AAPL",
    time_in_force=OrderBookTimeInForce.DAY,
):
    request = OrderBookOrderRequest(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=OrderBookOrderSide.BUY,
        order_type=OrderBookOrderType.MARKET,
        quantity=Decimal("100"),
        time_in_force=time_in_force,
    )
    return create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: NOW,
    )
