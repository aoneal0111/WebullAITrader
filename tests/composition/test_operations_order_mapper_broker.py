from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker_protocol.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerSide,
    TimeInForce,
)
from app.composition.operations_order_mapper import map_broker_orders


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _order(
    *,
    broker_order_id: str,
    symbol: str,
    side: BrokerSide,
    quantity: str,
    status: BrokerOrderStatus,
    updated_timestamp: datetime,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=f"client-{broker_order_id}",
        symbol=symbol,
        side=side,
        order_type=BrokerOrderType.MARKET,
        quantity=Decimal(quantity),
        filled_quantity=Decimal("0"),
        limit_price=None,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        status=status,
        updated_timestamp=updated_timestamp,
    )


def test_map_broker_orders_preserves_facts_and_sorts_newest_first() -> None:
    result = map_broker_orders(
        (
            _order(
                broker_order_id="older",
                symbol="aapl",
                side=BrokerSide.BUY,
                quantity="10.50",
                status=BrokerOrderStatus.SUBMITTED,
                updated_timestamp=NOW,
            ),
            _order(
                broker_order_id="newer",
                symbol="msft",
                side=BrokerSide.SELL,
                quantity="2",
                status=BrokerOrderStatus.PARTIALLY_FILLED,
                updated_timestamp=NOW + timedelta(minutes=1),
            ),
        )
    )

    assert tuple(order.order_id for order in result) == ("newer", "older")
    assert result[0].symbol == "MSFT"
    assert result[0].side == "SELL"
    assert result[0].quantity == "2"
    assert result[0].status == "PARTIALLY_FILLED"
    assert result[0].updated_at == NOW + timedelta(minutes=1)
    assert result[1].quantity == "10.50"


def test_map_broker_orders_requires_immutable_tuple() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        map_broker_orders([])  # type: ignore[arg-type]


def test_map_broker_orders_rejects_non_broker_order_members() -> None:
    with pytest.raises(TypeError, match="BrokerOrder"):
        map_broker_orders((object(),))  # type: ignore[arg-type]
