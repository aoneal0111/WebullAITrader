from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import AssetClass
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    OrderValidationError,
    accept_order,
    apply_fill,
    cancel_order,
    create_order,
    expire_order,
    reject_order,
)

D = Decimal
NOW = datetime(
    2026,
    7,
    20,
    14,
    0,
    tzinfo=UTC,
)


def market_request(
    **overrides: object,
) -> OrderRequest:
    values: dict[str, object] = {
        "symbol": "AAA",
        "asset_class": AssetClass.STOCK,
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": D("100"),
        "time_in_force": TimeInForce.DAY,
    }
    values.update(overrides)
    return OrderRequest(**values)


def new_order(
    **request_overrides: object,
):
    return create_order(
        market_request(**request_overrides),
        order_id_factory=lambda: "PAPER-1",
        clock=lambda: NOW,
    )


def accepted_order(
    **request_overrides: object,
):
    return accept_order(
        new_order(**request_overrides),
        at=NOW + timedelta(seconds=1),
    )


def test_order_request_normalizes_symbol() -> None:
    request = market_request(symbol="  aapl  ")

    assert request.symbol == "AAPL"


def test_quantity_must_be_positive() -> None:
    with pytest.raises(
        ValueError,
        match="quantity must be positive",
    ):
        market_request(quantity=D("0"))


def test_market_order_rejects_prices() -> None:
    with pytest.raises(
        ValueError,
        match="cannot specify prices",
    ):
        market_request(limit_price=D("5"))


def test_limit_order_requires_limit_price() -> None:
    with pytest.raises(
        ValueError,
        match="require limit_price",
    ):
        market_request(
            order_type=OrderType.LIMIT,
        )


def test_limit_order_accepts_limit_price() -> None:
    request = market_request(
        order_type=OrderType.LIMIT,
        limit_price=D("5.25"),
    )

    assert request.limit_price == D("5.25")


def test_stop_order_requires_stop_price() -> None:
    with pytest.raises(
        ValueError,
        match="require stop_price",
    ):
        market_request(
            order_type=OrderType.STOP,
        )


def test_stop_limit_requires_both_prices() -> None:
    with pytest.raises(
        ValueError,
        match="require both",
    ):
        market_request(
            order_type=OrderType.STOP_LIMIT,
            stop_price=D("5"),
        )


def test_prices_must_be_positive() -> None:
    with pytest.raises(
        ValueError,
        match="limit_price must be positive",
    ):
        market_request(
            order_type=OrderType.LIMIT,
            limit_price=D("0"),
        )


def test_create_order_sets_new_status() -> None:
    order = new_order()

    assert order.order_id == "PAPER-1"
    assert order.status is OrderStatus.NEW
    assert order.created_at == NOW
    assert order.updated_at == NOW
    assert order.remaining_quantity == D("100")
    assert order.is_terminal is False


def test_create_order_rejects_empty_identifier() -> None:
    with pytest.raises(
        OrderValidationError,
        match="empty value",
    ):
        create_order(
            market_request(),
            order_id_factory=lambda: " ",
            clock=lambda: NOW,
        )


def test_create_order_requires_aware_clock() -> None:
    with pytest.raises(
        OrderValidationError,
        match="timezone-aware",
    ):
        create_order(
            market_request(),
            order_id_factory=lambda: "PAPER-1",
            clock=lambda: datetime(2026, 7, 20),
        )


def test_new_order_can_be_accepted() -> None:
    order = accept_order(
        new_order(),
        at=NOW + timedelta(seconds=1),
    )

    assert order.status is OrderStatus.ACCEPTED
    assert order.updated_at == (
        NOW + timedelta(seconds=1)
    )


def test_new_order_can_be_rejected() -> None:
    order = reject_order(
        new_order(),
        "Insufficient simulated buying power",
        at=NOW + timedelta(seconds=1),
    )

    assert order.status is OrderStatus.REJECTED
    assert order.is_terminal is True
    assert order.rejection_reason == (
        "Insufficient simulated buying power"
    )


def test_rejection_requires_reason() -> None:
    with pytest.raises(
        OrderValidationError,
        match="reason is required",
    ):
        reject_order(
            new_order(),
            " ",
            at=NOW + timedelta(seconds=1),
        )


def test_new_order_can_be_cancelled() -> None:
    order = cancel_order(
        new_order(),
        at=NOW + timedelta(seconds=1),
    )

    assert order.status is OrderStatus.CANCELLED
    assert order.is_terminal is True


def test_fill_requires_accepted_order() -> None:
    with pytest.raises(
        InvalidOrderTransitionError,
        match="NEW",
    ):
        apply_fill(
            new_order(),
            D("10"),
            D("5"),
            at=NOW + timedelta(seconds=1),
        )


def test_partial_fill_updates_order() -> None:
    order = apply_fill(
        accepted_order(),
        D("40"),
        D("5"),
        at=NOW + timedelta(seconds=2),
    )

    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == D("40")
    assert order.remaining_quantity == D("60")
    assert order.average_fill_price == D("5")


def test_multiple_fills_calculate_weighted_average() -> None:
    order = apply_fill(
        accepted_order(),
        D("40"),
        D("5"),
        at=NOW + timedelta(seconds=2),
    )

    order = apply_fill(
        order,
        D("20"),
        D("8"),
        at=NOW + timedelta(seconds=3),
    )

    assert order.filled_quantity == D("60")
    assert order.remaining_quantity == D("40")
    assert order.average_fill_price == D("6")


def test_final_fill_marks_order_filled() -> None:
    order = apply_fill(
        accepted_order(),
        D("100"),
        D("5.50"),
        at=NOW + timedelta(seconds=2),
    )

    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == D("100")
    assert order.remaining_quantity == D("0")
    assert order.is_terminal is True


def test_fill_cannot_exceed_remaining_quantity() -> None:
    with pytest.raises(
        OrderValidationError,
        match="exceeds remaining",
    ):
        apply_fill(
            accepted_order(),
            D("101"),
            D("5"),
            at=NOW + timedelta(seconds=2),
        )


def test_terminal_order_cannot_transition_again() -> None:
    cancelled = cancel_order(
        new_order(),
        at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        InvalidOrderTransitionError,
        match="CANCELLED",
    ):
        accept_order(
            cancelled,
            at=NOW + timedelta(seconds=2),
        )


def test_partially_filled_order_can_expire() -> None:
    order = apply_fill(
        accepted_order(),
        D("25"),
        D("5"),
        at=NOW + timedelta(seconds=2),
    )

    order = expire_order(
        order,
        at=NOW + timedelta(seconds=3),
    )

    assert order.status is OrderStatus.EXPIRED
    assert order.filled_quantity == D("25")
    assert order.remaining_quantity == D("75")
    assert order.is_terminal is True



def test_apply_fill_records_execution_history() -> None:
    order = apply_fill(
        accepted_order(),
        D("40"),
        D("5"),
        at=NOW + timedelta(seconds=2),
        commission=D("1.25"),
        slippage=D("0.02"),
        venue=" paper ",
        liquidity_flag=" maker ",
        fill_id_factory=lambda: "FILL-1",
    )

    assert len(order.fills) == 1
    fill = order.fills[0]
    assert fill.fill_id == "FILL-1"
    assert fill.order_id == order.order_id
    assert fill.quantity == D("40")
    assert fill.price == D("5")
    assert fill.timestamp == NOW + timedelta(seconds=2)
    assert fill.commission == D("1.25")
    assert fill.slippage == D("0.02")
    assert fill.venue == "paper"
    assert fill.liquidity_flag == "MAKER"
    assert fill.notional == D("200")
    assert order.total_commission == D("1.25")
    assert order.total_slippage == D("0.02")


def test_multiple_fills_preserve_ordered_history() -> None:
    order = apply_fill(
        accepted_order(),
        D("40"),
        D("5"),
        at=NOW + timedelta(seconds=2),
        fill_id_factory=lambda: "FILL-1",
    )
    order = apply_fill(
        order,
        D("20"),
        D("8"),
        at=NOW + timedelta(seconds=3),
        commission=D("0.50"),
        slippage=D("0.01"),
        fill_id_factory=lambda: "FILL-2",
    )

    assert [fill.fill_id for fill in order.fills] == [
        "FILL-1",
        "FILL-2",
    ]
    assert order.filled_quantity == D("60")
    assert order.average_fill_price == D("6")
    assert order.total_commission == D("0.50")
    assert order.total_slippage == D("0.01")


def test_fill_rejects_negative_commission() -> None:
    with pytest.raises(
        OrderValidationError,
        match="commission cannot be negative",
    ):
        apply_fill(
            accepted_order(),
            D("10"),
            D("5"),
            commission=D("-0.01"),
            at=NOW + timedelta(seconds=2),
        )


def test_fill_id_factory_must_return_value() -> None:
    with pytest.raises(
        OrderValidationError,
        match="fill ID factory returned an empty value",
    ):
        apply_fill(
            accepted_order(),
            D("10"),
            D("5"),
            at=NOW + timedelta(seconds=2),
            fill_id_factory=lambda: " ",
        )
