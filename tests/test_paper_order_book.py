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
    cancel_order,
    create_order,
    expire_order,
    reject_order,
)

D = Decimal
NOW = datetime(2026, 7, 21, 14, 0, tzinfo=UTC)


def make_order(
    order_id: str,
    *,
    time_in_force: TimeInForce = TimeInForce.DAY,
    symbol: str = "AAPL",
):
    request = OrderRequest(
        symbol=symbol,
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


def test_active_indexes_follow_every_order_lifecycle_and_restore() -> None:
    book = PaperOrderBook()
    new = make_order("NEW")
    book.submit(new)
    assert book.open_orders_for_symbol("AAPL") == (new,)

    accepted = accept_order(new, at=NOW + timedelta(seconds=1))
    book.update(accepted)
    assert book.open_orders_for_symbol("AAPL") == (accepted,)

    partial = apply_fill(
        accepted,
        D("40"),
        D("10"),
        at=NOW + timedelta(seconds=2),
        fill_id_factory=lambda: "PARTIAL-FILL",
    )
    book.update(partial)
    assert partial.status is OrderStatus.PARTIALLY_FILLED
    assert book.open_orders_for_symbol("AAPL") == (partial,)

    filled = apply_fill(
        partial,
        D("60"),
        D("10"),
        at=NOW + timedelta(seconds=3),
        fill_id_factory=lambda: "FINAL-FILL",
    )
    book.update(filled)
    assert filled.status is OrderStatus.FILLED
    assert book.open_orders_for_symbol("AAPL") == ()

    terminal_factories = (
        lambda value: cancel_order(value, at=NOW + timedelta(seconds=2)),
        lambda value: expire_order(value, at=NOW + timedelta(seconds=2)),
    )
    for index, transition in enumerate(terminal_factories):
        active = accept_order(
            make_order(f"TERMINAL-{index}"),
            at=NOW + timedelta(seconds=1),
        )
        book.submit(active)
        book.update(transition(active))
        assert active.order_id not in {
            item.order_id for item in book.open_orders_for_symbol("AAPL")
        }

    rejected_new = make_order("REJECTED")
    book.submit(rejected_new)
    rejected = reject_order(
        rejected_new,
        "policy",
        at=NOW + timedelta(seconds=1),
    )
    book.update(rejected)
    assert rejected.status is OrderStatus.REJECTED
    assert rejected.order_id not in {
        item.order_id for item in book.open_orders_for_symbol("AAPL")
    }

    restored = accept_order(
        make_order("RESTORED", symbol="MSFT"),
        at=NOW + timedelta(seconds=1),
    )
    book.restore(restored)
    assert book.open_orders_for_symbol("msft") == (restored,)


def test_symbol_lookup_never_iterates_terminal_history() -> None:
    class HistoryGuard(dict):
        def values(self):
            raise AssertionError("symbol lookup scanned retained history")

        def __iter__(self):
            raise AssertionError("symbol lookup iterated retained history")

    book = PaperOrderBook()
    for index in range(2_000):
        terminal = reject_order(
            make_order(f"OLD-{index}"),
            "historical",
            at=NOW + timedelta(seconds=1),
        )
        book.restore(terminal)
    active = accept_order(
        make_order("ACTIVE", symbol="CDTG"),
        at=NOW + timedelta(seconds=1),
    )
    book.submit(active)
    book._orders = HistoryGuard(book._orders)

    assert book.open_orders_for_symbol("CDTG") == (active,)
