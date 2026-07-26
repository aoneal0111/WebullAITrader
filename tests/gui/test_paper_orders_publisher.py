from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.gui.orders import PaperOrdersPublisher
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OrdersUpdated,
)
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
)


class StubOrderHistory:
    def __init__(
        self,
        orders: tuple[PaperOrder, ...],
    ) -> None:
        self._orders = orders
        self.history_calls = 0

    def history(self) -> tuple[PaperOrder, ...]:
        self.history_calls += 1
        return self._orders


def make_order(
    *,
    order_id: str = "paper-order-1",
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("12.500"),
    status: OrderStatus = OrderStatus.ACCEPTED,
    updated_at: datetime | None = None,
) -> PaperOrder:
    timestamp = updated_at or datetime(
        2026,
        7,
        26,
        15,
        30,
        tzinfo=timezone.utc,
    )

    return PaperOrder(
        order_id=order_id,
        request=OrderRequest(
            symbol=symbol,
            asset_class=object(),
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
        ),
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_publish_replaces_application_order_state() -> None:
    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)
    order = make_order()
    order_history = StubOrderHistory((order,))
    publisher = PaperOrdersPublisher(
        bus=bus,
        order_book=order_history,
    )

    event = publisher.publish()

    assert isinstance(event, OrdersUpdated)
    assert event.source == "paper-orders"
    assert order_history.history_calls == 1
    assert len(event.orders) == 1

    projected = event.orders[0]

    assert projected.order_id == "paper-order-1"
    assert projected.symbol == "AAPL"
    assert projected.side == "BUY"
    assert projected.quantity == "12.500"
    assert projected.status == "ACCEPTED"
    assert projected.updated_at == order.updated_at
    assert state_store.snapshot().orders == event.orders

    state_store.close()


def test_publish_supports_an_empty_order_book() -> None:
    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)
    order_history = StubOrderHistory(())
    publisher = PaperOrdersPublisher(
        bus=bus,
        order_book=order_history,
    )

    event = publisher.publish()

    assert event.orders == ()
    assert state_store.snapshot().orders == ()
    assert order_history.history_calls == 1

    state_store.close()


def test_call_publishes_latest_order_snapshot() -> None:
    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)
    order = make_order(
        order_id="paper-order-2",
        symbol="MSFT",
        side=OrderSide.SELL,
        quantity=Decimal("3"),
        status=OrderStatus.NEW,
    )
    order_history = StubOrderHistory((order,))
    publisher = PaperOrdersPublisher(
        bus=bus,
        order_book=order_history,
    )

    event = publisher(object())

    assert event.orders[0].order_id == "paper-order-2"
    assert event.orders[0].symbol == "MSFT"
    assert event.orders[0].side == "SELL"
    assert event.orders[0].quantity == "3"
    assert event.orders[0].status == "NEW"
    assert state_store.snapshot().orders == event.orders

    state_store.close()
