import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_order():
    return api.create_submit_command(
        command_id="SUBMIT-1",
        order_id="ORDER-1",
        occurred_at=NOW,
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("10"),
        time_in_force="DAY",
        limit_price=Decimal("101.25"),
    ).payload


def test_public_factory_wraps_replacement_order_without_copying() -> None:
    order = make_order()
    command_id = "UPDATE-1"
    occurred_at = NOW

    command = api.create_update_command(
        command_id=command_id,
        order=order,
        occurred_at=occurred_at,
    )

    assert isinstance(command, api.PaperOrderBookCommand)
    assert command.command_type == "update"
    assert command.command_id is command_id
    assert command.payload is order
    assert command.occurred_at is occurred_at


def test_update_command_serialization_is_deterministic() -> None:
    first_order = make_order()
    second_order = make_order()

    first = api.create_update_command(
        command_id="UPDATE-1",
        order=first_order,
        occurred_at=NOW,
    )
    second = api.create_update_command(
        command_id="UPDATE-1",
        order=second_order,
        occurred_at=NOW,
    )

    first_serialized = api.serialize_command(first)
    assert first_serialized == api.serialize_command(first)
    assert first_serialized == api.serialize_command(second)
    assert first_serialized["command_type"] == "update"
    assert first_serialized["payload"]["value"]["order_id"] == "ORDER-1"


def test_factory_does_not_mutate_caller_owned_values() -> None:
    order = make_order()
    submit = api.PaperOrderBookCommand(
        command_id="SUBMIT-1",
        command_type="submit",
        payload=order,
        occurred_at=NOW,
    )
    order_before = api.serialize_command(submit)
    command_id = "UPDATE-1"
    occurred_at = NOW

    command = api.create_update_command(
        command_id=command_id,
        order=order,
        occurred_at=occurred_at,
    )

    assert api.serialize_command(submit) == order_before
    assert command_id == "UPDATE-1"
    assert occurred_at is NOW
    assert command.payload is order


def test_update_factory_test_imports_only_public_package_and_stdlib() -> None:
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
