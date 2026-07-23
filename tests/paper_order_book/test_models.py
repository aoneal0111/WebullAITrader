from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookCriteriaResult,
    PaperOrderBookIdentity,
    PaperOrderBookResult,
    PaperOrderBookSummary,
)
from tests.paper_order_book.helpers import NOW, make_lifecycle_request, make_order, make_request


def test_models_are_frozen_and_preserve_exact_objects() -> None:
    request = make_request()
    command = request.commands[0]

    assert request.snapshot.order_book is request.snapshot.order_book
    assert command.payload is command.payload
    assert request.commands[0] is command
    with pytest.raises(FrozenInstanceError):
        request.identity = PaperOrderBookIdentity("OTHER")
    with pytest.raises(FrozenInstanceError):
        command.command_id = "OTHER"


def test_observation_preserves_exact_mutable_book_without_copying_orders() -> None:
    request = make_request()
    book = request.snapshot.order_book
    existing = book.history()[0]

    assert request.snapshot.order_book is book
    assert request.snapshot.order_book.history()[0] is existing
    added = make_order("ORDER-2")
    book.submit(added)
    assert request.snapshot.order_book.history() == (existing, added)


def test_command_preserves_payload_identity_and_tuple_order() -> None:
    first_payload = make_lifecycle_request("MSFT")
    second_payload = make_order("ORDER-2")
    commands = (
        PaperOrderBookCommand(
            "C-1", "submit", first_payload, NOW + timedelta(seconds=1)
        ),
        PaperOrderBookCommand(
            "C-2", "observe", second_payload, NOW + timedelta(seconds=2)
        ),
    )
    request = make_request(commands=commands)

    assert request.commands is commands
    assert request.commands[0].payload is first_payload
    assert request.commands[1].payload is second_payload


def test_result_preserves_coordination_contracts() -> None:
    request = make_request()
    criteria = PaperOrderBookCriteriaResult(True)
    summary = PaperOrderBookSummary(1, 1)
    result = PaperOrderBookResult(
        identity=request.identity,
        snapshot=request.snapshot,
        commands=request.commands,
        summary=summary,
        criteria=criteria,
        requested_at=request.requested_at,
        completed_at=request.completed_at,
    )

    assert result.identity is request.identity
    assert result.snapshot is request.snapshot
    assert result.commands is request.commands
    assert result.summary is summary
    assert result.criteria is criteria
