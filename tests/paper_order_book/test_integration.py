import ast
from datetime import UTC, datetime
from pathlib import Path

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookPolicy,
    PaperOrderBookRequest,
    PaperOrderBookResult,
    create_observation,
    create_request,
    execute,
    serialize_request,
    serialize_result,
    validate_request,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def build_public_request() -> PaperOrderBookRequest:
    return create_request(
        identity=PaperOrderBookIdentity("PUBLIC-BOOK-1"),
        policy=PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
        commands=(),
    )


def test_public_package_constructs_valid_request_and_command_contract() -> None:
    request = build_public_request()
    observation = create_observation(
        identity=request.identity,
        captured_at=request.completed_at,
    )
    command = PaperOrderBookCommand(
        command_id="OBSERVE-1",
        command_type="observe",
        payload=observation.order_book,
        occurred_at=NOW,
    )

    assert isinstance(request, PaperOrderBookRequest)
    assert request.commands == ()
    assert isinstance(command, PaperOrderBookCommand)
    assert command.payload is observation.order_book
    assert validate_request(request).accepted is True


def test_public_execute_preserves_request_identity_and_state() -> None:
    request = build_public_request()
    identity = request.identity
    observation = request.snapshot
    book = observation.order_book
    before = serialize_request(request)

    result = execute(request)

    assert isinstance(result, PaperOrderBookResult)
    assert result.identity is identity
    assert result.snapshot.identity is identity
    assert result.snapshot.order_book is book
    assert result.snapshot.captured_at is observation.captured_at
    assert request.snapshot is observation
    assert request.snapshot.order_book is book
    assert book.history() == ()
    assert serialize_request(request) == before


def test_equivalent_public_requests_execute_deterministically_by_value() -> None:
    first_request = build_public_request()
    second_request = build_public_request()

    first_before = serialize_request(first_request)
    second_before = serialize_request(second_request)
    first_result = execute(first_request)
    second_result = execute(second_request)

    assert first_request.snapshot.order_book is not (
        second_request.snapshot.order_book
    )
    assert first_before == second_before
    assert serialize_request(first_request) == first_before
    assert serialize_request(second_request) == second_before
    assert serialize_result(first_result) == serialize_result(second_result)
    assert serialize_result(first_result) == serialize_result(first_result)


def test_integration_module_imports_only_public_package_and_stdlib() -> None:
    path = Path(__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
