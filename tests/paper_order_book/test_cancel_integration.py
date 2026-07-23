import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import app.paper_order_book as api

SUBMITTED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
CANCELLED_AT = SUBMITTED_AT + timedelta(minutes=1)


def make_submit_command() -> api.PaperOrderBookCommand:
    return api.create_submit_command(
        command_id="SUBMIT-1",
        order_id="ORDER-1",
        occurred_at=SUBMITTED_AT,
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("10"),
        time_in_force="DAY",
        limit_price=Decimal("101.25"),
        client_order_id="CLIENT-1",
    )


def execute_cancellation_workflow():
    identity = api.PaperOrderBookIdentity("PUBLIC-CANCEL-BOOK")
    policy = api.PaperOrderBookPolicy()
    submit_command = make_submit_command()
    submit_request = api.create_request(
        identity=identity,
        policy=policy,
        requested_at=SUBMITTED_AT,
        completed_at=SUBMITTED_AT,
        commands=(submit_command,),
    )
    assert api.validate_request(submit_request).accepted is True
    submit_result = api.execute(submit_request)
    submitted_order = submit_result.snapshot.order_book.history()[0]

    cancel_command = api.create_cancel_command(
        command_id="CANCEL-1",
        order=submitted_order,
        occurred_at=CANCELLED_AT,
    )
    cancel_request = api.PaperOrderBookRequest(
        identity=identity,
        snapshot=submit_result.snapshot,
        commands=(cancel_command,),
        requested_at=CANCELLED_AT,
        completed_at=CANCELLED_AT,
        policy=policy,
    )
    assert api.validate_request(cancel_request).accepted is True
    cancel_result = api.execute(cancel_request)
    return (
        submit_command,
        submit_request,
        submit_result,
        submitted_order,
        cancel_command,
        cancel_request,
        cancel_result,
    )


def test_public_submit_then_cancel_workflow_preserves_order_contract() -> None:
    (
        _,
        _,
        _,
        submitted_order,
        cancel_command,
        _,
        cancel_result,
    ) = execute_cancellation_workflow()
    cancelled_order = cancel_result.snapshot.order_book.history()[0]

    assert cancelled_order.status.value == "CANCELLED"
    assert cancelled_order.order_id == submitted_order.order_id == "ORDER-1"
    assert cancelled_order.created_at is submitted_order.created_at
    assert cancelled_order.created_at is SUBMITTED_AT
    assert cancelled_order.updated_at is CANCELLED_AT
    assert cancelled_order.request is submitted_order.request
    assert cancelled_order.symbol == "AAPL"
    assert cancelled_order.quantity == Decimal("10")
    assert cancelled_order.side.value == "BUY"
    assert cancelled_order.order_type.value == "LIMIT"
    assert cancelled_order.request.limit_price == Decimal("101.25")
    assert cancelled_order.request.client_order_id == "CLIENT-1"
    assert cancel_command.payload is submitted_order
    assert cancelled_order is not submitted_order


def test_independent_cancel_workflows_are_deterministic() -> None:
    first = execute_cancellation_workflow()
    second = execute_cancellation_workflow()
    first_cancel_request = first[5]
    second_cancel_request = second[5]
    first_cancel_result = first[6]
    second_cancel_result = second[6]

    assert first_cancel_request.snapshot.order_book is not (
        second_cancel_request.snapshot.order_book
    )
    assert api.serialize_request(first_cancel_request) == (
        api.serialize_request(second_cancel_request)
    )
    assert api.serialize_result(first_cancel_result) == (
        api.serialize_result(second_cancel_result)
    )
    assert api.serialize_result(first_cancel_result) == (
        api.serialize_result(first_cancel_result)
    )


def test_cancellation_does_not_mutate_caller_owned_envelopes() -> None:
    identity = api.PaperOrderBookIdentity("PUBLIC-CANCEL-BOOK")
    policy = api.PaperOrderBookPolicy()
    submit_command = make_submit_command()
    submit_request = api.create_request(
        identity=identity,
        policy=policy,
        requested_at=SUBMITTED_AT,
        completed_at=SUBMITTED_AT,
        commands=(submit_command,),
    )
    submit_result = api.execute(submit_request)
    submitted_order = submit_result.snapshot.order_book.history()[0]
    cancel_command = api.create_cancel_command(
        command_id="CANCEL-1",
        order=submitted_order,
        occurred_at=CANCELLED_AT,
    )
    cancel_request = api.PaperOrderBookRequest(
        identity=identity,
        snapshot=submit_result.snapshot,
        commands=(cancel_command,),
        requested_at=CANCELLED_AT,
        completed_at=CANCELLED_AT,
        policy=policy,
    )
    command_before = api.serialize_command(cancel_command)
    request_identity = cancel_request.identity
    request_snapshot = cancel_request.snapshot
    request_commands = cancel_request.commands

    cancel_result = api.execute(cancel_request)

    assert cancel_request.identity is request_identity
    assert cancel_request.snapshot is request_snapshot
    assert cancel_request.commands is request_commands
    assert cancel_request.commands[0] is cancel_command
    assert cancel_command.payload is submitted_order
    assert cancel_command.occurred_at is CANCELLED_AT
    assert api.serialize_command(cancel_command) == command_before
    assert submitted_order.status.value == "NEW"
    assert submitted_order.updated_at is SUBMITTED_AT
    assert cancel_result.snapshot.order_book is request_snapshot.order_book


def test_cancel_integration_imports_only_public_package_and_stdlib() -> None:
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
