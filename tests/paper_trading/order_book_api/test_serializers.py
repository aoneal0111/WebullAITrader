from datetime import timedelta
from decimal import Decimal

import pytest

from app.paper_trading.order_book_api import (
    OrderBookSerializationError,
    PaperOrderBook,
    accept_order,
    apply_fill,
    serialize_order_book,
    serialize_order_book_fill,
    serialize_order_book_order,
    serialize_order_book_request,
)
from tests.paper_trading.order_book_api import NOW, make_order


def test_serializers_use_stable_public_shapes() -> None:
    accepted = accept_order(make_order("PAPER-1"), at=NOW + timedelta(seconds=1))
    filled = apply_fill(
        accepted,
        Decimal("25"),
        Decimal("10.50"),
        at=NOW + timedelta(seconds=2),
        commission=Decimal("1.25"),
        fill_id_factory=lambda: "FILL-1",
    )
    fill = filled.fills[0]

    request_data = serialize_order_book_request(filled.request)
    fill_data = serialize_order_book_fill(fill)
    order_data = serialize_order_book_order(filled)

    assert request_data["quantity"] == "100"
    assert request_data["side"] == "BUY"
    assert request_data["limit_price"] is None
    assert fill_data == {
        "fill_id": "FILL-1",
        "order_id": "PAPER-1",
        "quantity": "25",
        "price": "10.50",
        "timestamp": (NOW + timedelta(seconds=2)).isoformat(),
        "commission": "1.25",
        "slippage": "0",
        "venue": None,
        "liquidity_flag": None,
    }
    assert order_data["status"] == "PARTIALLY_FILLED"
    assert order_data["fills"] == [fill_data]
    assert order_data["average_fill_price"] == "10.50"


def test_book_serializer_observes_history_and_preserves_insertion_order() -> None:
    book = PaperOrderBook()
    first = make_order("FIRST")
    second = make_order("SECOND")
    book.submit(first)
    book.submit(second)

    first_serialization = serialize_order_book(book)
    second_serialization = serialize_order_book(book)

    assert first_serialization == second_serialization
    assert [item["order_id"] for item in first_serialization["orders"]] == [
        "FIRST",
        "SECOND",
    ]
    assert book.history() == (first, second)


@pytest.mark.parametrize(
    ("serializer", "value"),
    [
        (serialize_order_book_request, {}),
        (serialize_order_book_fill, None),
        (serialize_order_book_order, "order"),
        (serialize_order_book, object()),
    ],
)
def test_serializers_reject_unsupported_inputs(serializer, value) -> None:
    with pytest.raises(OrderBookSerializationError):
        serializer(value)
