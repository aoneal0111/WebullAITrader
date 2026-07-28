from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_scanner import AssetClass
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_lifecycle import (
    PaperOrderLifecycleCoordinator,
    evaluate_order,
)
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.paper_trading.orders import accept_order, create_order

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
D = Decimal


def accepted_order(
    *,
    order_id: str = "PAPER-1",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: str = "10",
    time_in_force: TimeInForce = TimeInForce.DAY,
    limit_price: str | None = None,
    stop_price: str | None = None,
):
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=side,
        order_type=order_type,
        quantity=D(quantity),
        time_in_force=time_in_force,
        limit_price=D(limit_price) if limit_price is not None else None,
        stop_price=D(stop_price) if stop_price is not None else None,
    )
    created = create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: NOW,
    )
    return accept_order(created, at=NOW + timedelta(seconds=1))


def test_market_order_fills_completely_and_publishes_event() -> None:
    book = PaperOrderBook()
    order = accepted_order()
    book.submit(order)
    events = []
    coordinator = PaperOrderLifecycleCoordinator(
        book,
        listeners=(events.append,),
    )

    updated = coordinator.process_order(
        order.order_id,
        market_price=D("192.50"),
        at=NOW + timedelta(seconds=2),
    )

    assert updated.status is OrderStatus.FILLED
    assert updated.filled_quantity == D("10")
    assert updated.average_fill_price == D("192.50")
    assert book.get(order.order_id) is updated
    assert len(events) == 1
    assert events[0].previous_status is OrderStatus.ACCEPTED
    assert events[0].current_status is OrderStatus.FILLED
    assert events[0].fill_price == D("192.50")


def test_available_quantity_produces_partial_fill() -> None:
    book = PaperOrderBook()
    order = accepted_order(quantity="10")
    book.submit(order)
    coordinator = PaperOrderLifecycleCoordinator(book)

    updated = coordinator.process_order(
        order.order_id,
        market_price=D("100"),
        available_quantity=D("4"),
        at=NOW + timedelta(seconds=2),
    )

    assert updated.status is OrderStatus.PARTIALLY_FILLED
    assert updated.filled_quantity == D("4")
    assert updated.remaining_quantity == D("6")


def test_buy_limit_waits_above_limit_and_fills_at_market_below_limit() -> None:
    order = accepted_order(
        order_type=OrderType.LIMIT,
        limit_price="100",
    )

    waiting = evaluate_order(order, D("101"))
    executable = evaluate_order(order, D("99"))

    assert waiting.executable is False
    assert waiting.fill_price is None
    assert executable.executable is True
    assert executable.fill_price == D("99")


def test_sell_stop_triggers_when_market_reaches_stop() -> None:
    order = accepted_order(
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        stop_price="95",
    )

    waiting = evaluate_order(order, D("96"))
    triggered = evaluate_order(order, D("94"))

    assert waiting.executable is False
    assert triggered.executable is True
    assert triggered.fill_price == D("94")


def test_ioc_order_cancels_when_not_executable() -> None:
    book = PaperOrderBook()
    order = accepted_order(
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.IOC,
        limit_price="100",
    )
    book.submit(order)
    coordinator = PaperOrderLifecycleCoordinator(book)

    updated = coordinator.process_order(
        order.order_id,
        market_price=D("101"),
        at=NOW + timedelta(seconds=2),
    )

    assert updated.status is OrderStatus.CANCELLED
    assert updated.filled_quantity == D("0")


def test_ioc_partial_fill_cancels_remaining_quantity() -> None:
    book = PaperOrderBook()
    order = accepted_order(
        quantity="10",
        time_in_force=TimeInForce.IOC,
    )
    book.submit(order)
    events = []
    coordinator = PaperOrderLifecycleCoordinator(
        book,
        listeners=(events.append,),
    )

    updated = coordinator.process_order(
        order.order_id,
        market_price=D("100"),
        available_quantity=D("3"),
        at=NOW + timedelta(seconds=2),
    )

    assert updated.status is OrderStatus.CANCELLED
    assert updated.filled_quantity == D("3")
    assert updated.remaining_quantity == D("7")
    assert [event.current_status for event in events] == [
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCELLED,
    ]


def test_process_open_orders_uses_symbol_price_mapping() -> None:
    book = PaperOrderBook()
    first = accepted_order(order_id="PAPER-1")
    second = accepted_order(order_id="PAPER-2")
    book.submit(first)
    book.submit(second)
    coordinator = PaperOrderLifecycleCoordinator(book)

    processed = coordinator.process_open_orders(
        {"aapl": D("123.45")},
        at=NOW + timedelta(seconds=2),
    )

    assert len(processed) == 2
    assert all(order.status is OrderStatus.FILLED for order in processed)
