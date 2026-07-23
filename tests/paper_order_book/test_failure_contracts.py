import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookCriteriaResult,
    PaperOrderBookIdentity,
    PaperOrderBookPolicy,
    create_observation,
    create_request,
    execute,
    serialize_criteria,
    serialize_request,
    serialize_result,
    validate_request,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def build_timestamp_invalid_request():
    return create_request(
        identity=PaperOrderBookIdentity("INVALID-BOOK"),
        policy=PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )


def build_duplicate_invalid_request():
    identity = PaperOrderBookIdentity("DUPLICATE-BOOK")
    observation = create_observation(identity=identity, captured_at=NOW)
    commands = (
        PaperOrderBookCommand(
            "DUP", "expire_day_orders", observation.order_book, NOW
        ),
        PaperOrderBookCommand(
            "DUP", "expire_day_orders", observation.order_book, NOW
        ),
    )
    return create_request(
        identity=identity,
        policy=PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
        commands=commands,
    )


def test_invalid_requests_return_rejected_validation_criteria() -> None:
    timestamp_criteria = validate_request(build_timestamp_invalid_request())
    duplicate_criteria = validate_request(build_duplicate_invalid_request())

    assert timestamp_criteria.accepted is False
    assert timestamp_criteria.errors == (
        "captured_at cannot follow requested_at",
    )
    assert duplicate_criteria.accepted is False
    assert duplicate_criteria.errors == (
        "duplicate command_id at command index 1: DUP",
    )


def test_repeated_invalid_validation_and_serialization_are_deterministic() -> None:
    first = validate_request(build_timestamp_invalid_request())
    second = validate_request(build_timestamp_invalid_request())

    assert first == second
    assert serialize_criteria(first) == serialize_criteria(second)
    assert serialize_criteria(first) == {
        "accepted": False,
        "errors": ["captured_at cannot follow requested_at"],
    }


def test_validation_never_mutates_invalid_request() -> None:
    request = build_duplicate_invalid_request()
    book = request.snapshot.order_book
    commands = request.commands
    before = serialize_request(request)

    validate_request(request)

    assert serialize_request(request) == before
    assert request.commands is commands
    assert request.snapshot.order_book is book
    assert book.history() == ()


def test_execute_returns_deterministic_rejected_result_for_invalid_request() -> None:
    first_request = build_timestamp_invalid_request()
    second_request = build_timestamp_invalid_request()

    first = execute(first_request)
    second = execute(second_request)

    assert first.criteria == PaperOrderBookCriteriaResult(
        accepted=False,
        errors=("captured_at cannot follow requested_at",),
    )
    assert first.errors == first.criteria.errors
    assert first.snapshot is first_request.snapshot
    assert serialize_result(first) == serialize_result(second)


def test_validation_failures_expose_plain_public_data_only() -> None:
    criteria = validate_request(build_duplicate_invalid_request())
    serialized = serialize_criteria(criteria)

    assert isinstance(criteria, PaperOrderBookCriteriaResult)
    assert all(isinstance(error, str) for error in criteria.errors)
    assert set(serialized) == {"accepted", "errors"}
    assert all(isinstance(error, str) for error in serialized["errors"])


def test_valid_factory_request_remains_valid_after_invalid_validation() -> None:
    validate_request(build_timestamp_invalid_request())
    valid = create_request(
        identity=PaperOrderBookIdentity("VALID-BOOK"),
        policy=PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
    )

    assert validate_request(valid).accepted is True
    assert validate_request(valid).errors == ()


def test_independent_invalid_requests_do_not_share_state() -> None:
    first = build_timestamp_invalid_request()
    second = build_timestamp_invalid_request()

    assert first is not second
    assert first.snapshot is not second.snapshot
    assert first.snapshot.order_book is not second.snapshot.order_book
    assert validate_request(first) == validate_request(second)


def test_failure_contract_module_imports_only_public_package_and_stdlib() -> None:
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
