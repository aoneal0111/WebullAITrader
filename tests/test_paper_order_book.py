from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import AssetClass
from app.paper_trading.order_book import (
    DuplicateOrderError,
    OrderBookError,
    OrderNotFoundError,
    PaperOrderBook,
    StaleOrderUpdateError,
)
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.paper_trading.orders import (
    accept_order,
    apply_fill,
    create_order,
)

D = Decimal
NOW = datetime(2026, 7, 21, 14, 0, tzinfo=UTC)


def make_order(
    order_id: str,
    *,
    time_in_force: TimeInForce = TimeInForce.DAY,
):
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=D("100"),
        time_in_force=time_in_force,
    )
    return create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: NOW,
    )


def test_submit_get_contains_and_history_preserve_order() -> None:
    book = PaperOrderBook()
    first = make_order("PAPER-1")
    second = make_order("PAPER-2")

    assert book.submit(first) is first
    book.submit(second)

    assert len(book) == 2
    assert book.contains(" PAPER-1 ") is True
    assert book.get("PAPER-1") is first
    assert book.history() == (first, second)


def test_submit_rejects_duplicate_order_id() -> None:
    book = PaperOrderBook()
    book.submit(make_order("PAPER-1"))

    with pytest.raises(DuplicateOrderError, match="already exists"):
        book.submit(make_order("PAPER-1"))


def test_get_rejects_unknown_order() -> None:
    book = PaperOrderBook()

    with pytest.raises(OrderNotFoundError, match="was not found"):
        book.get("MISSING")


def test_update_replaces_immutable_snapshot() -> None:
    book = PaperOrderBook()
    original = make_order("PAPER-1")
    accepted = accept_order(original, at=NOW + timedelta(seconds=1))

    book.submit(original)
    book.update(accepted)

    assert book.get("PAPER-1") is accepted
    assert book.open_orders() == (accepted,)


def test_update_rejects_changed_created_at() -> None:
    book = PaperOrderBook()
    original = make_order("PAPER-1")
    replacement = make_order("PAPER-1")
    replacement = replacement.__class__(
        order_id=replacement.order_id,
        request=replacement.request,
        status=replacement.status,
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    )
    book.submit(original)

    with pytest.raises(OrderBookError, match="preserve created_at"):
        book.update(replacement)


def test_update_rejects_stale_snapshot() -> None:
    book = PaperOrderBook()
    original = make_order("PAPER-1")
    accepted = accept_order(original, at=NOW + timedelta(seconds=2))
    book.submit(accepted)

    with pytest.raises(StaleOrderUpdateError, match="stale"):
        book.update(original)


def test_cancel_moves_order_to_terminal_index() -> None:
    book = PaperOrderBook()
    accepted = accept_order(
        make_order("PAPER-1"),
        at=NOW + timedelta(seconds=1),
    )
    book.submit(accepted)

    cancelled = book.cancel(
        "PAPER-1",
        at=NOW + timedelta(seconds=2),
    )

    assert cancelled.status is OrderStatus.CANCELLED
    assert book.open_orders() == ()
    assert book.terminal_orders() == (cancelled,)


def test_filled_order_is_terminal_after_update() -> None:
    book = PaperOrderBook()
    accepted = accept_order(
        make_order("PAPER-1"),
        at=NOW + timedelta(seconds=1),
    )
    filled = apply_fill(
        accepted,
        D("100"),
        D("10"),
        at=NOW + timedelta(seconds=2),
        fill_id_factory=lambda: "FILL-1",
    )
    book.submit(accepted)
    book.update(filled)

    assert book.open_orders() == ()
    assert book.terminal_orders() == (filled,)


def test_expire_day_orders_skips_gtc_and_returns_expired() -> None:
    book = PaperOrderBook()
    day_order = accept_order(
        make_order("DAY-1"),
        at=NOW + timedelta(seconds=1),
    )
    gtc_order = accept_order(
        make_order("GTC-1", time_in_force=TimeInForce.GTC),
        at=NOW + timedelta(seconds=1),
    )
    book.submit(day_order)
    book.submit(gtc_order)

    expired = book.expire_day_orders(
        at=NOW + timedelta(hours=8)
    )

    assert len(expired) == 1
    assert expired[0].order_id == "DAY-1"
    assert expired[0].status is OrderStatus.EXPIRED
    assert book.open_orders() == (gtc_order,)
    assert book.terminal_orders() == expired


def test_empty_order_id_is_rejected() -> None:
    book = PaperOrderBook()

    with pytest.raises(ValueError, match="order_id is required"):
        book.contains("   ")
