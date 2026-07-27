from datetime import datetime, timezone

import pytest

from app.read_models.orders.models import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_order() -> OrderReadModel:
    return OrderReadModel(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW,
    )


def test_order_read_model_is_immutable() -> None:
    order = make_order()

    with pytest.raises(AttributeError):
        order.status = "FILLED"  # type: ignore[misc]


def test_order_read_model_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="updated_at must be timezone-aware",
    ):
        OrderReadModel(
            order_id="order-1",
            symbol="AAPL",
            side="BUY",
            quantity="10",
            status="ACCEPTED",
            updated_at=datetime(2026, 7, 27, 12, 0),
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "order_id",
        "symbol",
        "side",
        "quantity",
        "status",
    ),
)
def test_order_read_model_rejects_blank_text(
    field_name: str,
) -> None:
    values = {
        "order_id": "order-1",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": "10",
        "status": "ACCEPTED",
        "updated_at": NOW,
    }
    values[field_name] = " "

    with pytest.raises(
        ValueError,
        match=f"{field_name} must not be empty",
    ):
        OrderReadModel(**values)


def test_initial_orders_snapshot_is_empty() -> None:
    assert OrdersReadModelSnapshot.initial().orders == ()


def test_orders_snapshot_requires_immutable_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="orders must be an immutable tuple",
    ):
        OrdersReadModelSnapshot(
            orders=[make_order()],  # type: ignore[arg-type]
        )
