from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.paper_trading.order_book_api as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_fill(**overrides):
    values = {
        "fill_id": "FILL-1",
        "order_id": "ORDER-1",
        "quantity": Decimal("2"),
        "price": Decimal("100.50"),
        "occurred_at": NOW,
    }
    values.update(overrides)
    return api.create_fill(**values)


def test_factory_constructs_existing_fill_with_exact_required_values() -> None:
    fill = make_fill()

    assert isinstance(fill, api.OrderBookFill)
    assert fill.fill_id == "FILL-1"
    assert fill.order_id == "ORDER-1"
    assert fill.quantity == Decimal("2")
    assert fill.price == Decimal("100.50")
    assert fill.timestamp is NOW
    assert fill.commission == Decimal("0")
    assert fill.slippage == Decimal("0")
    assert fill.venue is None
    assert fill.liquidity_flag is None


def test_factory_delegates_normalization_to_existing_model() -> None:
    fill = make_fill(
        fill_id=" FILL-1 ",
        order_id=" ORDER-1 ",
        commission=Decimal("1.25"),
        slippage=Decimal("0.05"),
        venue=" paper ",
        liquidity_flag=" maker ",
    )

    assert fill.fill_id == "FILL-1"
    assert fill.order_id == "ORDER-1"
    assert fill.commission == Decimal("1.25")
    assert fill.slippage == Decimal("0.05")
    assert fill.venue == "paper"
    assert fill.liquidity_flag == "MAKER"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fill_id", " ", "fill_id is required"),
        ("order_id", " ", "order_id is required"),
        ("quantity", Decimal("0"), "quantity must be positive"),
        ("price", Decimal("0"), "price must be positive"),
        ("commission", Decimal("-1"), "commission cannot be negative"),
        (
            "occurred_at",
            datetime(2026, 7, 22, 12, 0),
            "timestamp must be timezone-aware",
        ),
    ],
)
def test_existing_model_validation_remains_authoritative(
    field, value, message
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        make_fill(**{field: value})


def test_equivalent_calls_serialize_identically_without_generated_values() -> None:
    first = make_fill()
    second = make_fill()

    assert first == second
    assert first is not second
    assert api.serialize_order_book_fill(first) == (
        api.serialize_order_book_fill(second)
    )
    assert first.fill_id == "FILL-1"
    assert first.timestamp is NOW
