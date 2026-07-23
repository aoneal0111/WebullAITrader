from datetime import timedelta
from decimal import Decimal

import app.paper_trading as root_api
import app.paper_trading.order_book_api as api
from app.paper_trading.fill_models import Fill
from app.paper_trading.milestone_models import (
    PaperFill as RootPaperFill,
    PaperOrder as RootPaperOrder,
    PaperOrderStatus as RootPaperOrderStatus,
)
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    TimeInForce,
)
from tests.paper_trading.order_book_api import NOW, make_order


def test_aliases_preserve_existing_lifecycle_identities() -> None:
    assert api.PaperOrderBook is PaperOrderBook
    assert api.OrderBookPaperOrder is PaperOrder
    assert api.OrderBookFill is Fill
    assert api.OrderBookOrderRequest is OrderRequest
    assert api.OrderBookOrderStatus is OrderStatus
    assert api.OrderBookOrderSide is OrderSide
    assert api.OrderBookOrderType is OrderType
    assert api.OrderBookTimeInForce is TimeInForce


def test_existing_root_colliding_exports_remain_unchanged() -> None:
    assert root_api.PaperOrder is RootPaperOrder
    assert root_api.PaperFill is RootPaperFill
    assert root_api.PaperOrderStatus is RootPaperOrderStatus
    assert root_api.PaperOrder is not api.OrderBookPaperOrder
    assert root_api.PaperFill is not api.OrderBookFill
    assert root_api.PaperOrderStatus is not api.OrderBookOrderStatus
    assert "OrderBookPaperOrder" not in root_api.__all__


def test_public_all_is_intentional() -> None:
    assert set(api.__all__) == {
        "PaperOrderBook",
        "PaperOrderBookInterface",
        "OrderBookPaperOrder",
        "OrderBookFill",
        "OrderBookOrderRequest",
        "OrderBookOrderStatus",
        "OrderBookOrderSide",
        "OrderBookOrderType",
        "OrderBookTimeInForce",
        "create_order",
        "accept_order",
        "reject_order",
        "cancel_order",
        "expire_order",
        "apply_fill",
        "serialize_order_book_request",
        "serialize_order_book_fill",
        "serialize_order_book_order",
        "serialize_order_book",
        "OrderBookError",
        "OrderBookValidationError",
        "OrderBookSerializationError",
        "DuplicateOrderError",
        "OrderNotFoundError",
        "StaleOrderUpdateError",
        "InvalidOrderTransitionError",
    }


def test_book_behavior_is_unchanged_through_facade() -> None:
    book = api.PaperOrderBook()
    first = make_order("PAPER-1")
    second = make_order("PAPER-2", symbol="MSFT")

    assert book.submit(first) is first
    book.submit(second)
    assert book.history() == (first, second)
    assert book.get(" PAPER-1 ") is first
    assert book.contains("PAPER-2")
    assert book.open_orders_for_symbol("aapl") == (first,)

    accepted = api.accept_order(first, at=NOW + timedelta(seconds=1))
    assert book.update(accepted) is accepted
    filled = api.apply_fill(
        accepted,
        Decimal("100"),
        Decimal("10"),
        at=NOW + timedelta(seconds=2),
        fill_id_factory=lambda: "FILL-1",
    )
    assert book.update(filled) is filled
    assert book.terminal_orders() == (filled,)
    assert book.open_orders() == (second,)
    assert filled.fills[0].fill_id == "FILL-1"


def test_cancel_and_expire_behavior_is_unchanged() -> None:
    book = api.PaperOrderBook()
    cancelled_source = api.accept_order(
        make_order("CANCEL-1"), at=NOW + timedelta(seconds=1)
    )
    day_source = api.accept_order(
        make_order("DAY-1"), at=NOW + timedelta(seconds=1)
    )
    gtc_source = api.accept_order(
        make_order("GTC-1", time_in_force=api.OrderBookTimeInForce.GTC),
        at=NOW + timedelta(seconds=1),
    )
    book.submit(cancelled_source)
    book.submit(day_source)
    book.submit(gtc_source)

    cancelled = book.cancel("CANCEL-1", at=NOW + timedelta(seconds=2))
    expired = book.expire_day_orders(at=NOW + timedelta(hours=8))

    assert cancelled.status is api.OrderBookOrderStatus.CANCELLED
    assert tuple(order.order_id for order in expired) == ("DAY-1",)
    assert book.open_orders() == (gtc_source,)
    assert tuple(order.order_id for order in book.terminal_orders()) == (
        "CANCEL-1",
        "DAY-1",
    )
    assert tuple(order.order_id for order in book.history()) == (
        "CANCEL-1",
        "DAY-1",
        "GTC-1",
    )
