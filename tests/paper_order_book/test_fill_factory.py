import ast
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.paper_order_book as api

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


def test_public_factory_constructs_valid_fill_and_preserves_values() -> None:
    fill_id = "FILL-1"
    order_id = "ORDER-1"
    quantity = Decimal("2")
    price = Decimal("100.50")
    occurred_at = NOW

    fill = api.create_fill(
        fill_id=fill_id,
        order_id=order_id,
        quantity=quantity,
        price=price,
        occurred_at=occurred_at,
    )

    assert "create_fill" in api.__all__
    assert fill.fill_id == fill_id
    assert fill.order_id == order_id
    assert fill.quantity is quantity
    assert fill.price is price
    assert fill.timestamp is occurred_at
    assert fill.commission == Decimal("0")
    assert fill.slippage == Decimal("0")


def test_public_factory_inherits_lifecycle_normalization() -> None:
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


def test_equivalent_public_construction_is_deterministic() -> None:
    first = make_fill()
    second = make_fill()

    assert first == second
    assert first is not second
    assert asdict(first) == asdict(second)


def test_lifecycle_validation_propagates_without_mutating_inputs() -> None:
    quantity = Decimal("0")

    with pytest.raises(ValueError, match="^quantity must be positive$"):
        make_fill(quantity=quantity)

    assert quantity == Decimal("0")


def test_fill_factory_test_imports_only_public_package_and_stdlib() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    application_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            application_imports.update(
                alias.name for alias in node.names if alias.name.startswith("app.")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("app.")
        ):
            application_imports.add(node.module)

    assert application_imports == {"app.paper_order_book"}
