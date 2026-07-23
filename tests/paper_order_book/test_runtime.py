from datetime import timedelta

import pytest

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookRuntime,
    PaperOrderBookValidationError,
)
from tests.paper_order_book.helpers import NOW, make_order, make_request


def test_evaluation_is_deterministic_and_preserves_exact_objects() -> None:
    request = make_request()
    runtime = PaperOrderBookRuntime()

    first = runtime.evaluate(request)
    second = runtime.evaluate(request)

    assert first == second
    assert first is not second
    assert first.identity is request.identity
    assert first.snapshot is request.snapshot
    assert first.commands is request.commands
    assert first.commands[0] is request.commands[0]


def test_summary_counts_observed_orders_and_original_commands() -> None:
    payload = make_order("ORDER-2")
    commands = (
        PaperOrderBookCommand(
            "C-1", "observe", payload, NOW + timedelta(seconds=1)
        ),
        PaperOrderBookCommand(
            "C-2", "observe", payload, NOW + timedelta(seconds=2)
        ),
    )
    request = make_request(commands=commands)

    result = PaperOrderBookRuntime().evaluate(request)

    assert result.summary.initial_orders == 1
    assert result.summary.command_count == 2


def test_rejected_criteria_are_returned_as_a_deterministic_result() -> None:
    payload = make_order("ORDER-2")
    commands = (
        PaperOrderBookCommand(
            "DUP", "submit", payload, NOW + timedelta(seconds=1)
        ),
        PaperOrderBookCommand(
            "DUP", "submit", payload, NOW + timedelta(seconds=2)
        ),
    )
    request = make_request(
        commands=commands,
        identity=PaperOrderBookIdentity("REQUEST"),
        snapshot_identity=PaperOrderBookIdentity("OBSERVATION"),
    )

    result = PaperOrderBookRuntime().evaluate(request)

    assert result.criteria.accepted is False
    assert result.criteria.errors == (
        "snapshot identity must match request identity",
        "duplicate command_id at command index 1: DUP",
    )
    assert result.errors == result.criteria.errors
    assert result.summary.command_count == 2


def test_runtime_never_mutates_the_caller_owned_order_book() -> None:
    request = make_request()
    book = request.snapshot.order_book
    before = book.history()

    PaperOrderBookRuntime().evaluate(request)

    assert request.snapshot.order_book is book
    assert book.history() == before
    assert book.history()[0] is before[0]


def test_only_invalid_request_type_raises_from_evaluation() -> None:
    with pytest.raises(PaperOrderBookValidationError):
        PaperOrderBookRuntime().evaluate(object())
