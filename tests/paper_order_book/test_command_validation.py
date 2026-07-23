import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def submit_command():
    return api.create_submit_command(
        command_id="SUBMIT-1",
        order_id="ORDER-1",
        occurred_at=NOW,
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("10"),
        time_in_force="DAY",
    )


def request_with(command):
    return api.create_request(
        identity=api.PaperOrderBookIdentity("BOOK-1"),
        policy=api.PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
        commands=(command,),
    )


def book_payload():
    return api.create_observation(
        identity=api.PaperOrderBookIdentity("PAYLOAD-BOOK"),
        captured_at=NOW,
    ).order_book


def envelope(command_type, payload):
    return api.PaperOrderBookCommand(
        command_id="COMMAND-1",
        command_type=command_type,
        payload=payload,
        occurred_at=NOW,
    )


def test_valid_submit_command_is_accepted() -> None:
    assert api.validate_request(request_with(submit_command())).accepted is True


def test_submit_with_book_payload_is_rejected() -> None:
    criteria = api.validate_request(
        request_with(envelope("submit", book_payload()))
    )
    assert criteria.errors == (
        "invalid payload for command_type submit at command index 0",
    )


def test_expire_day_orders_with_order_payload_is_rejected() -> None:
    criteria = api.validate_request(
        request_with(envelope("expire_day_orders", submit_command().payload))
    )
    assert criteria.errors == (
        "invalid payload for command_type expire_day_orders at command index 0",
    )


def test_order_commands_with_book_payload_are_rejected_in_request_order() -> None:
    for command_type in ("update", "cancel", "accept", "expire"):
        criteria = api.validate_request(
            request_with(envelope(command_type, book_payload()))
        )
        assert criteria.errors == (
            f"invalid payload for command_type {command_type} at command index 0",
        )


def test_apply_fill_with_order_payload_is_rejected() -> None:
    criteria = api.validate_request(
        request_with(envelope("apply_fill", submit_command().payload))
    )
    assert criteria.errors == (
        "invalid payload for command_type apply_fill at command index 0",
    )


def test_unknown_command_type_is_rejected() -> None:
    criteria = api.validate_request(
        request_with(envelope("replace", submit_command().payload))
    )
    assert criteria.errors == (
        "unsupported command_type at command index 0: replace",
    )


def test_repeated_validation_and_criteria_serialization_are_deterministic() -> None:
    request = request_with(envelope("submit", book_payload()))
    first = api.validate_request(request)
    second = api.validate_request(request)

    assert first == second
    assert api.serialize_criteria(first) == api.serialize_criteria(second)


def test_validation_does_not_mutate_any_caller_owned_contract() -> None:
    order = submit_command().payload
    command = envelope("apply_fill", order)
    request = request_with(command)
    observation = request.snapshot
    request_before = api.serialize_request(request)
    command_before = api.serialize_command(command)

    api.validate_request(request)

    assert api.serialize_request(request) == request_before
    assert api.serialize_command(command) == command_before
    assert command.payload is order
    assert observation.order_book.history() == ()
    assert order.order_id == "ORDER-1"


def test_command_validation_imports_only_public_package_and_stdlib() -> None:
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
