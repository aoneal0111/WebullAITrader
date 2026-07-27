from datetime import datetime, timezone
from decimal import Decimal

from app.broker_protocol.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerSide,
    TimeInForce,
)
from app.composition.broker_order_projection import create_broker_orders_publisher
from app.operations_core import ApplicationStateStore, OperationsBus


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_broker_orders_publisher_updates_application_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    publisher = create_broker_orders_publisher(bus)

    try:
        publisher(
            (
                BrokerOrder(
                    broker_order_id="broker-1",
                    client_order_id="client-1",
                    symbol="aapl",
                    side=BrokerSide.BUY,
                    order_type=BrokerOrderType.LIMIT,
                    quantity=Decimal("10"),
                    filled_quantity=Decimal("0"),
                    limit_price=Decimal("185.25"),
                    stop_price=None,
                    time_in_force=TimeInForce.DAY,
                    status=BrokerOrderStatus.ACKNOWLEDGED,
                    updated_timestamp=NOW,
                ),
            ),
            NOW,
        )

        order = store.snapshot().orders[0]
        assert order.order_id == "broker-1"
        assert order.symbol == "AAPL"
        assert order.side == "BUY"
        assert order.quantity == "10"
        assert order.status == "ACKNOWLEDGED"
        assert order.updated_at == NOW
    finally:
        store.close()
