from datetime import timedelta

import pytest

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookValidationError,
    validate_request,
)
from tests.paper_order_book.helpers import NOW, make_lifecycle_request, make_request


def test_valid_request_is_accepted_without_mutating_book() -> None:
    request = make_request()
    history_before = request.snapshot.order_book.history()

    first = validate_request(request)
    second = validate_request(request)

    assert first == second
    assert first.accepted is True
    assert first.errors == ()
    assert request.snapshot.order_book.history() == history_before


def test_duplicate_ids_and_timestamp_errors_are_deterministic_in_order() -> None:
    payload = make_lifecycle_request()
    commands = (
        PaperOrderBookCommand(
            "DUP", "submit", payload, NOW + timedelta(seconds=3)
        ),
        PaperOrderBookCommand(
            "DUP", "submit", payload, NOW + timedelta(seconds=2)
        ),
    )
    request = make_request(commands=commands)

    criteria = validate_request(request)

    assert criteria.accepted is False
    assert criteria.errors == (
        "duplicate command_id at command index 1: DUP",
        "command timestamps are not monotonic at command index 1",
    )


def test_identity_and_request_timestamp_inconsistency_are_rejected() -> None:
    request = make_request(
        identity=PaperOrderBookIdentity("REQUEST"),
        snapshot_identity=PaperOrderBookIdentity("SNAPSHOT"),
    )
    object.__setattr__(
        request,
        "completed_at",
        request.requested_at - timedelta(seconds=1),
    )

    criteria = validate_request(request)

    assert criteria.errors[:2] == (
        "snapshot identity must match request identity",
        "completed_at cannot precede requested_at",
    )


def test_wrong_request_type_raises_public_validation_error() -> None:
    with pytest.raises(PaperOrderBookValidationError):
        validate_request(object())


def test_invalid_payload_type_is_rejected_by_command_contract() -> None:
    with pytest.raises(PaperOrderBookValidationError, match="payload"):
        PaperOrderBookCommand("C-1", "submit", {}, NOW)
