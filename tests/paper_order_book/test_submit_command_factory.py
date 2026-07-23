import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_command(**overrides):
    values = {
        "command_id": "COMMAND-1",
        "order_id": "ORDER-1",
        "occurred_at": NOW,
        "symbol": "AAPL",
        "asset_class": "STOCK",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": Decimal("10"),
        "time_in_force": "DAY",
        "limit_price": Decimal("101.25"),
    }
    values.update(overrides)
    return api.create_submit_command(**values)


def test_public_factory_constructs_complete_submit_command() -> None:
    command = make_command()

    assert isinstance(command, api.PaperOrderBookCommand)
    assert command.command_type == "submit"
    assert command.command_id == "COMMAND-1"
    assert command.payload.order_id == "ORDER-1"
    assert command.occurred_at is NOW
    assert command.payload.created_at is NOW
    assert command.payload.updated_at is NOW


def test_serialization_is_deterministic_for_equivalent_inputs() -> None:
    first = api.serialize_command(make_command())
    second = api.serialize_command(make_command())

    assert first == second
    assert first["command_id"] == "COMMAND-1"
    assert first["command_type"] == "submit"
    assert first["payload"]["type"] == "order"
    assert first["payload"]["value"]["order_id"] == "ORDER-1"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("asset_class", "BOND", "asset_class is invalid"),
        ("side", "HOLD", "side is invalid"),
        ("order_type", "PEGGED", "order_type is invalid"),
        ("time_in_force", "FOK", "time_in_force is invalid"),
    ],
)
def test_lifecycle_validation_propagates_deterministically(
    field, value, message
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        make_command(**{field: value})


def test_factory_test_imports_only_public_package_and_stdlib() -> None:
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
