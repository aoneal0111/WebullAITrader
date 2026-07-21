from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import AssetClass
from app.paper_trading.execution_engine import (
    ExecutionEngineError,
    PaperExecutionEngine,
)
from app.paper_trading.matching_engine import MarketQuote
from app.paper_trading.order_book import DuplicateOrderError
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.paper_trading.orders import (
    accept_order,
    create_order,
)

D = Decimal
NOW = datetime(2026, 7, 21, 16, 0, tzinfo=UTC)


def make_order(
    order_id: str = "PAPER-1",
    *,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: Decimal = D("100"),
    limit_price: Decimal | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
):
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=side,
        order_type=order_type,
        quantity=quantity,
        time_in_force=time_in_force,
        limit_price=limit_price,
    )

    created = create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: NOW,
    )

    return accept_order(
        created,
        at=NOW + timedelta(seconds=1),
    )


def quote(
    *,
    symbol: str = "AAPL",
    bid: str = "99",
    ask: str = "101",
    volume: str = "100",
    seconds: int = 2,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        bid_price=D(bid),
        ask_price=D(ask),
        available_volume=D(volume),
        last_trade_price=D("100"),
        timestamp=NOW + timedelta(seconds=seconds),
    )

def fill_ids():
    count = 0

    def next_id() -> str:
        nonlocal count
        count += 1
        return f"FILL-{count}"

    return next_id


def test_submit_stores_accepted_order() -> None:
    engine = PaperExecutionEngine()
    order = make_order()

    report = engine.submit(order)

    assert report.order is order
    assert report.match_result is None
    assert report.fills == ()
    assert engine.order_book.get(order.order_id) is order
    assert engine.order_book.open_orders() == (order,)


def test_submit_rejects_unaccepted_order() -> None:
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=D("10"),
        time_in_force=TimeInForce.DAY,
    )

    created = create_order(
        request,
        order_id_factory=lambda: "PAPER-NEW",
        clock=lambda: NOW,
    )

    engine = PaperExecutionEngine()

    with pytest.raises(
        ExecutionEngineError,
        match="only accepted orders",
    ):
        engine.submit(created)


def test_duplicate_submit_is_rejected() -> None:
    engine = PaperExecutionEngine()
    order = make_order()

    engine.submit(order)

    with pytest.raises(
        DuplicateOrderError,
        match="already exists",
    ):
        engine.submit(order)


def test_market_quote_fully_fills_order() -> None:
    engine = PaperExecutionEngine(
        fill_id_factory=fill_ids(),
    )
    engine.submit(make_order())

    reports = engine.process_quote(quote())

    assert len(reports) == 1

    report = reports[0]

    assert report.matched is True
    assert report.order.status is OrderStatus.FILLED
    assert report.order.filled_quantity == D("100")
    assert report.order.remaining_quantity == D("0")
    assert report.fills[0].fill_id == "FILL-1"
    assert report.fills[0].quantity == D("100")
    assert report.fills[0].price == D("101")
    assert engine.order_book.open_orders() == ()
    assert engine.order_book.terminal_orders() == (
        report.order,
    )


def test_insufficient_volume_creates_partial_fill() -> None:
    engine = PaperExecutionEngine(
        fill_id_factory=fill_ids(),
    )
    engine.submit(make_order())

    report = engine.process_quote(
        quote(volume="40")
    )[0]

    assert report.order.status is OrderStatus.PARTIALLY_FILLED
    assert report.order.filled_quantity == D("40")
    assert report.order.remaining_quantity == D("60")
    assert report.fills[0].quantity == D("40")
    assert engine.order_book.open_orders() == (
        report.order,
    )


def test_multiple_quotes_complete_partial_order() -> None:
    engine = PaperExecutionEngine(
        fill_id_factory=fill_ids(),
    )
    engine.submit(make_order())

    first = engine.process_quote(
        quote(volume="40")
    )[0]

    second = engine.process_quote(
        quote(
            volume="60",
            ask="102",
            seconds=3,
        )
    )[0]

    assert first.order.status is OrderStatus.PARTIALLY_FILLED
    assert second.order.status is OrderStatus.FILLED
    assert second.order.filled_quantity == D("100")
    assert second.order.remaining_quantity == D("0")

    assert [
        fill.fill_id
        for fill in second.order.fills
    ] == [
        "FILL-1",
        "FILL-2",
    ]

    assert second.order.average_fill_price == D("101.6")


def test_non_crossing_limit_order_remains_open() -> None:
    engine = PaperExecutionEngine(
        fill_id_factory=fill_ids(),
    )

    order = make_order(
        order_type=OrderType.LIMIT,
        limit_price=D("100"),
    )

    engine.submit(order)

    report = engine.process_quote(
        quote(ask="101")
    )[0]

    assert report.matched is False
    assert report.order is order
    assert report.fills == ()
    assert report.order.status is OrderStatus.ACCEPTED
    assert engine.order_book.open_orders() == (order,)


def test_completed_order_is_not_double_filled() -> None:
    engine = PaperExecutionEngine(
        fill_id_factory=fill_ids(),
    )
    engine.submit(make_order())

    first_reports = engine.process_quote(quote())
    second_reports = engine.process_quote(
        quote(seconds=3)
    )

    assert len(first_reports) == 1
    assert second_reports == ()

    stored = engine.order_book.get("PAPER-1")

    assert stored.status is OrderStatus.FILLED
    assert len(stored.fills) == 1
    assert stored.filled_quantity == D("100")


def test_cancel_delegates_to_order_book() -> None:
    engine = PaperExecutionEngine()
    engine.submit(make_order())

    report = engine.cancel(
        "PAPER-1",
        at=NOW + timedelta(seconds=2),
    )

    assert report.order.status is OrderStatus.CANCELLED
    assert report.fills == ()
    assert engine.order_book.open_orders() == ()
    assert engine.order_book.terminal_orders() == (
        report.order,
    )


def test_expire_day_orders_skips_gtc_orders() -> None:
    engine = PaperExecutionEngine()

    day_order = make_order(
        "DAY-1",
        time_in_force=TimeInForce.DAY,
    )
    gtc_order = make_order(
        "GTC-1",
        time_in_force=TimeInForce.GTC,
    )

    engine.submit(day_order)
    engine.submit(gtc_order)

    reports = engine.expire_day_orders(
        at=NOW + timedelta(hours=8),
    )

    assert len(reports) == 1
    assert reports[0].order.order_id == "DAY-1"
    assert reports[0].order.status is OrderStatus.EXPIRED
    assert engine.order_book.open_orders() == (gtc_order,)


def test_process_quote_with_no_orders_returns_empty_tuple() -> None:
    engine = PaperExecutionEngine()

    assert engine.process_quote(quote()) == ()