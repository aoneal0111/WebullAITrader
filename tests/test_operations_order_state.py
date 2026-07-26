from datetime import datetime, timezone

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
    OrdersUpdated,
    RuntimeStarted,
)


NOW = datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc)


def make_order(
    *,
    order_id: str = "order-1",
    symbol: str = "AAPL",
    side: str = "BUY",
    quantity: str = "10",
    status: str = "ACCEPTED",
) -> OperationsOrder:
    return OperationsOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        updated_at=NOW,
    )


def test_initial_application_state_has_no_orders() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        snapshot = store.snapshot()

        assert snapshot.orders == ()
        assert snapshot.revision == 0
    finally:
        store.close()


def test_orders_updated_replaces_the_order_slice() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    first = make_order()
    second = make_order(
        order_id="order-2",
        symbol="MSFT",
        side="SELL",
        quantity="5",
        status="PARTIALLY_FILLED",
    )

    try:
        bus.publish(
            OrdersUpdated(
                source="paper-order-book",
                orders=(first, second),
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.orders == (first, second)
        assert snapshot.revision == 1
        assert snapshot.timeline[-1].event_type == "OrdersUpdated"
        assert snapshot.timeline[-1].source == "paper-order-book"
        assert snapshot.timeline[-1].message == (
            "Order state updated: 2 orders."
        )
    finally:
        store.close()


def test_empty_orders_updated_clears_the_order_slice() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        bus.publish(
            OrdersUpdated(
                orders=(make_order(),),
                occurred_at=NOW,
            )
        )
        bus.publish(
            OrdersUpdated(
                orders=(),
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.orders == ()
        assert snapshot.revision == 2
        assert snapshot.timeline[-1].message == (
            "Order state updated: 0 orders."
        )
    finally:
        store.close()


def test_runtime_events_preserve_existing_orders() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    order = make_order()

    try:
        bus.publish(
            OrdersUpdated(
                orders=(order,),
                occurred_at=NOW,
            )
        )
        bus.publish(
            RuntimeStarted(
                environment="PAPER",
                active_model="Atlas Test Model",
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.orders == (order,)
        assert snapshot.runtime.active_model == "Atlas Test Model"
        assert snapshot.revision == 2
    finally:
        store.close()