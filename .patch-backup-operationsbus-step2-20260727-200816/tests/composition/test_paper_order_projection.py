from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.composition.paper_order_projection import (
    create_paper_order_lifecycle_publisher,
    map_paper_orders,
)
from app.momentum_scanner import AssetClass
from app.operations_core import OperationsBus, OrdersUpdated
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_lifecycle import OrderLifecycleEvent
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.paper_trading.orders import accept_order, create_order

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
D = Decimal


def accepted_order(order_id: str, *, symbol: str = "AAPL"):
    request = OrderRequest(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=D("10"),
    )
    created = create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: NOW,
    )
    return accept_order(created, at=NOW + timedelta(seconds=1))


def test_map_paper_orders_is_deterministic_and_backend_neutral() -> None:
    first = accepted_order("PAPER-1", symbol="aapl")
    second = accepted_order("PAPER-2", symbol="msft")

    mapped = map_paper_orders((first, second))

    assert tuple(order.order_id for order in mapped) == ("PAPER-2", "PAPER-1")
    assert mapped[0].symbol == "MSFT"
    assert mapped[0].side == "BUY"
    assert mapped[0].quantity == "10"
    assert mapped[0].status == "ACCEPTED"
    assert mapped[0].updated_at == NOW + timedelta(seconds=1)


def test_lifecycle_publisher_emits_current_order_book_snapshot() -> None:
    bus = OperationsBus()
    book = PaperOrderBook()
    first = accepted_order("PAPER-1")
    second = accepted_order("PAPER-2", symbol="MSFT")
    book.submit(first)
    book.submit(second)
    received: list[OrdersUpdated] = []
    bus.subscribe(OrdersUpdated, received.append)
    publisher = create_paper_order_lifecycle_publisher(bus, book)
    occurred_at = NOW + timedelta(seconds=2)

    publisher(
        OrderLifecycleEvent(
            order_id=first.order_id,
            previous_status=OrderStatus.ACCEPTED,
            current_status=OrderStatus.FILLED,
            occurred_at=occurred_at,
            filled_quantity=D("10"),
            remaining_quantity=D("0"),
            fill_price=D("192.50"),
        )
    )

    assert len(received) == 1
    event = received[0]
    assert event.source == "paper-order-lifecycle"
    assert event.occurred_at == occurred_at
    assert tuple(order.order_id for order in event.orders) == (
        "PAPER-2",
        "PAPER-1",
    )


def test_projection_rejects_mutable_or_invalid_inputs() -> None:
    bus = OperationsBus()
    book = PaperOrderBook()

    try:
        map_paper_orders([])  # type: ignore[arg-type]
    except TypeError as exc:
        assert str(exc) == "orders must be an immutable tuple"
    else:
        raise AssertionError("mutable order collection should be rejected")

    publisher = create_paper_order_lifecycle_publisher(bus, book)
    try:
        publisher(object())  # type: ignore[arg-type]
    except TypeError as exc:
        assert str(exc) == "event must be an OrderLifecycleEvent"
    else:
        raise AssertionError("invalid lifecycle event should be rejected")
