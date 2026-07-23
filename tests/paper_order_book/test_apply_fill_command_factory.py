import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_fill():
    return api.create_fill(
        fill_id="FILL-1",
        order_id="ORDER-1",
        quantity=Decimal("2"),
        price=Decimal("100.50"),
        occurred_at=NOW,
        commission=Decimal("1.25"),
        slippage=Decimal("0.05"),
        venue="paper",
        liquidity_flag="MAKER",
    )


def test_public_factory_wraps_existing_fill_without_copying() -> None:
    fill = make_fill()
    command_id = "APPLY-FILL-1"
    occurred_at = NOW

    command = api.create_apply_fill_command(
        command_id=command_id,
        fill=fill,
        occurred_at=occurred_at,
    )

    assert isinstance(command, api.PaperOrderBookCommand)
    assert command.command_type == "apply_fill"
    assert command.command_id is command_id
    assert command.payload is fill
    assert command.occurred_at is occurred_at


def test_equivalent_commands_serialize_deterministically() -> None:
    first = api.create_apply_fill_command(
        command_id="APPLY-FILL-1",
        fill=make_fill(),
        occurred_at=NOW,
    )
    second = api.create_apply_fill_command(
        command_id="APPLY-FILL-1",
        fill=make_fill(),
        occurred_at=NOW,
    )

    assert first == second
    assert first is not second
    assert api.serialize_command(first) == api.serialize_command(second)
    assert api.serialize_command(first) == api.serialize_command(first)


def test_factory_does_not_mutate_caller_owned_values() -> None:
    fill = make_fill()
    fill_before = (
        fill.fill_id,
        fill.order_id,
        fill.quantity,
        fill.price,
        fill.timestamp,
        fill.commission,
        fill.slippage,
        fill.venue,
        fill.liquidity_flag,
    )
    command_id = "APPLY-FILL-1"
    occurred_at = NOW

    command = api.create_apply_fill_command(
        command_id=command_id,
        fill=fill,
        occurred_at=occurred_at,
    )

    assert command_id == "APPLY-FILL-1"
    assert occurred_at is NOW
    assert command.payload is fill
    assert fill_before == (
        fill.fill_id,
        fill.order_id,
        fill.quantity,
        fill.price,
        fill.timestamp,
        fill.commission,
        fill.slippage,
        fill.venue,
        fill.liquidity_flag,
    )


def test_apply_fill_factory_imports_only_public_package_and_stdlib() -> None:
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
