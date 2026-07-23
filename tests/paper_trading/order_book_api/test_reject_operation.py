from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import app.paper_trading.order_book_api as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_order():
    return api.create_submission_order(
        order_id="ORDER-1",
        occurred_at=NOW,
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("10"),
        time_in_force="DAY",
        limit_price=Decimal("101.25"),
    )


def test_public_reject_operation_reuses_immutable_transition() -> None:
    book = api.PaperOrderBook()
    order = make_order()
    book.submit(order)
    rejected_at = NOW + timedelta(seconds=1)

    rejected = api.reject(
        book,
        order,
        " Risk limit exceeded ",
        at=rejected_at,
    )

    assert rejected is book.get(order.order_id)
    assert rejected is not order
    assert rejected.order_id == order.order_id
    assert rejected.request is order.request
    assert rejected.created_at is order.created_at
    assert rejected.updated_at is rejected_at
    assert rejected.status is api.OrderBookOrderStatus.REJECTED
    assert rejected.rejection_reason == "Risk limit exceeded"


def test_public_reject_operation_preserves_required_reason_failure() -> None:
    book = api.PaperOrderBook()
    order = make_order()
    book.submit(order)

    with pytest.raises(
        api.OrderBookValidationError,
        match="^rejection reason is required$",
    ):
        api.reject(book, order, " ", at=NOW + timedelta(seconds=1))

    assert book.get(order.order_id) is order
