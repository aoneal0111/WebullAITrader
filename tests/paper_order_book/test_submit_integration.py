import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_submit_command(
    *,
    command_id: str = "COMMAND-1",
    order_id: str = "ORDER-1",
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("10"),
) -> api.PaperOrderBookCommand:
    return api.create_submit_command(
        command_id=command_id,
        order_id=order_id,
        occurred_at=NOW,
        symbol=symbol,
        asset_class="STOCK",
        side="BUY",
        order_type="LIMIT",
        quantity=quantity,
        time_in_force="DAY",
        limit_price=Decimal("101.25"),
    )


def make_request(
    commands: tuple[api.PaperOrderBookCommand, ...],
) -> api.PaperOrderBookRequest:
    return api.create_request(
        identity=api.PaperOrderBookIdentity("PUBLIC-SUBMIT-BOOK"),
        policy=api.PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
        commands=commands,
    )


def test_public_submit_executes_through_complete_application_stack() -> None:
    command = make_submit_command()
    request = make_request((command,))

    assert api.validate_request(request).accepted is True

    result = api.execute(request)
    history = result.snapshot.order_book.history()

    assert result.criteria.accepted is True
    assert len(result.snapshot.order_book) == 1
    assert len(history) == 1
    assert history[0].order_id == "ORDER-1"
    assert history[0].symbol == "AAPL"
    assert history[0].quantity == Decimal("10")
    assert history[0].created_at is NOW
    assert history[0].updated_at is NOW
    assert result.requested_at is NOW
    assert result.completed_at is NOW
    assert result.snapshot.captured_at is NOW
    assert result.commands[0] is command
    assert result.snapshot.order_book.history() == history


def test_equivalent_submit_requests_execute_deterministically() -> None:
    first_request = make_request((make_submit_command(),))
    second_request = make_request((make_submit_command(),))

    first_result = api.execute(first_request)
    second_result = api.execute(second_request)

    assert first_request.snapshot.order_book is not (
        second_request.snapshot.order_book
    )
    assert api.serialize_result(first_result) == api.serialize_result(
        second_result
    )
    assert api.serialize_snapshot(first_result.snapshot) == (
        api.serialize_snapshot(second_result.snapshot)
    )


def test_two_public_submit_commands_preserve_all_ordering() -> None:
    first = make_submit_command(
        command_id="COMMAND-1",
        order_id="ORDER-1",
        symbol="AAPL",
    )
    second = make_submit_command(
        command_id="COMMAND-2",
        order_id="ORDER-2",
        symbol="MSFT",
        quantity=Decimal("20"),
    )
    request = make_request((first, second))

    result = api.execute(request)
    history = result.snapshot.order_book.history()

    assert result.commands is request.commands
    assert tuple(command.command_id for command in result.commands) == (
        "COMMAND-1",
        "COMMAND-2",
    )
    assert tuple(order.order_id for order in history) == (
        "ORDER-1",
        "ORDER-2",
    )
    assert tuple(order.symbol for order in history) == ("AAPL", "MSFT")


def test_submit_serialization_is_deterministic_before_and_after_execution() -> None:
    request = make_request((make_submit_command(),))

    before = api.serialize_request(request)
    assert api.serialize_request(request) == before

    result = api.execute(request)
    after_request = api.serialize_request(request)
    after_result = api.serialize_result(result)

    assert api.serialize_request(request) == after_request
    assert api.serialize_result(result) == after_result
    assert before["snapshot"]["order_book"]["orders"] == []
    assert len(after_request["snapshot"]["order_book"]["orders"]) == 1
    assert len(after_result["snapshot"]["order_book"]["orders"]) == 1


def test_submit_execution_preserves_caller_owned_request_structure() -> None:
    command = make_submit_command()
    request = make_request((command,))
    identity = request.identity
    policy = request.policy
    commands = request.commands
    observation = request.snapshot
    order_book = observation.order_book
    command_before = api.serialize_command(command)

    result = api.execute(request)

    assert request.identity is identity
    assert request.policy is policy
    assert request.commands is commands
    assert request.commands[0] is command
    assert request.snapshot is observation
    assert request.snapshot.order_book is order_book
    assert request.requested_at is NOW
    assert request.completed_at is NOW
    assert api.serialize_command(command) == command_before
    assert result.snapshot.order_book is order_book
    assert order_book.history()[0] is command.payload


def test_submit_integration_imports_only_public_package_and_stdlib() -> None:
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
