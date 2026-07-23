import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import app.paper_order_book as api

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_valid(book_id: str = "VALID"):
    return api.create_request(
        identity=api.PaperOrderBookIdentity(book_id),
        policy=api.PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
    )


def make_invalid(book_id: str = "INVALID"):
    return api.create_request(
        identity=api.PaperOrderBookIdentity(book_id),
        policy=api.PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )


def test_documented_entry_points_and_valid_end_to_end_contract() -> None:
    for name in (
        "execute",
        "create_request",
        "create_observation",
        "create_service",
        "default_service",
    ):
        assert name in api.__all__
        assert callable(getattr(api, name))

    request = make_valid()
    request_data = api.serialize_request(request)
    result = api.execute(request)

    assert api.validate_request(request).accepted is True
    assert api.serialize_request(request) == request_data
    assert isinstance(result, api.PaperOrderBookResult)
    assert api.serialize_result(result) == api.serialize_result(result)


def test_invalid_public_request_is_deterministic_and_not_mutated() -> None:
    request = make_invalid()
    before = api.serialize_request(request)
    first = api.validate_request(request)
    second = api.validate_request(request)

    assert first.accepted is False
    assert first == second
    assert api.serialize_criteria(first) == api.serialize_criteria(second)
    assert api.serialize_request(request) == before


def test_equivalent_valid_and_invalid_requests_have_equal_public_outputs() -> None:
    valid_one = make_valid()
    valid_two = make_valid()
    invalid_one = make_invalid()
    invalid_two = make_invalid()

    assert api.serialize_request(valid_one) == api.serialize_request(valid_two)
    assert api.serialize_result(api.execute(valid_one)) == api.serialize_result(
        api.execute(valid_two)
    )
    assert api.validate_request(invalid_one) == api.validate_request(invalid_two)
    assert api.serialize_result(api.execute(invalid_one)) == api.serialize_result(
        api.execute(invalid_two)
    )


def test_failed_validation_does_not_contaminate_later_repeated_execution() -> None:
    rejected = api.execute(make_invalid())
    valid_one = api.execute(make_valid())
    valid_two = api.execute(make_valid())

    assert rejected.criteria.accepted is False
    assert valid_one.criteria.accepted is True
    assert api.serialize_result(valid_one) == api.serialize_result(valid_two)


def test_contract_matrix_imports_only_public_package_and_stdlib() -> None:
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
