from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.paper_trading.order_book_api as api
import app.paper_trading.orders as order_helpers

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_order(**overrides):
    values = {
        "order_id": "ORDER-1",
        "occurred_at": NOW,
        "symbol": " aapl ",
        "asset_class": "STOCK",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": Decimal("10"),
        "time_in_force": "DAY",
        "limit_price": Decimal("101.25"),
        "client_order_id": "CLIENT-1",
    }
    values.update(overrides)
    return api.create_submission_order(**values)


def test_operation_is_exported_and_constructs_existing_order() -> None:
    order = make_order()

    assert "create_submission_order" in api.__all__
    assert isinstance(order, api.OrderBookPaperOrder)
    assert order.order_id == "ORDER-1"
    assert order.created_at is NOW
    assert order.updated_at is NOW
    assert order.status is api.OrderBookOrderStatus.NEW
    assert order.request.symbol == "AAPL"
    assert order.request.asset_class.value == "STOCK"
    assert order.request.side is api.OrderBookOrderSide.BUY
    assert order.request.order_type is api.OrderBookOrderType.LIMIT
    assert order.request.time_in_force is api.OrderBookTimeInForce.DAY
    assert order.request.quantity == Decimal("10")
    assert order.request.limit_price == Decimal("101.25")
    assert order.request.stop_price is None
    assert order.request.client_order_id == "CLIENT-1"


def test_public_enum_instances_are_accepted() -> None:
    order = make_order(
        side=api.OrderBookOrderSide.SELL,
        order_type=api.OrderBookOrderType.MARKET,
        time_in_force=api.OrderBookTimeInForce.GTC,
        limit_price=None,
    )

    assert order.request.side is api.OrderBookOrderSide.SELL
    assert order.request.order_type is api.OrderBookOrderType.MARKET
    assert order.request.time_in_force is api.OrderBookTimeInForce.GTC


def test_equivalent_inputs_serialize_identically() -> None:
    assert api.serialize_order_book_order(make_order()) == (
        api.serialize_order_book_order(make_order())
    )


def test_explicit_factories_prevent_uuid_and_clock_defaults(monkeypatch) -> None:
    def forbidden():
        raise AssertionError("hidden generator was called")

    monkeypatch.setattr(order_helpers, "_new_order_id", forbidden)
    monkeypatch.setattr(order_helpers, "_utc_now", forbidden)

    order = make_order()

    assert order.order_id == "ORDER-1"
    assert order.created_at is NOW


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("asset_class", "BOND", "asset_class is invalid"),
        ("side", "HOLD", "side is invalid"),
        ("order_type", "PEGGED", "order_type is invalid"),
        ("time_in_force", "FOK", "time_in_force is invalid"),
    ],
)
def test_unknown_enum_values_are_rejected_deterministically(
    field, value, message
) -> None:
    with pytest.raises(api.OrderBookValidationError, match=f"^{message}$"):
        make_order(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": Decimal("0")},
        {"limit_price": None},
        {"limit_price": Decimal("-1")},
    ],
)
def test_existing_lifecycle_validation_remains_authoritative(overrides) -> None:
    with pytest.raises(ValueError):
        make_order(**overrides)
