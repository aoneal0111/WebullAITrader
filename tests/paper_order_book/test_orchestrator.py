from datetime import timedelta

import app.paper_trading.order_book_api as lifecycle_api

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookOrchestrator,
    PaperOrderBookRuntime,
)
from tests.paper_order_book.helpers import NOW, make_order, make_request


class RecordingRuntime:
    def __init__(self, events):
        self.events = events
        self.result = None

    def evaluate(self, request):
        self.events.append("evaluate")
        self.result = PaperOrderBookRuntime().evaluate(request)
        return self.result


def test_runtime_is_invoked_before_lifecycle_delegation(monkeypatch) -> None:
    events = []
    runtime = RecordingRuntime(events)
    order = make_order("ORDER-2")
    command = PaperOrderBookCommand(
        "SUBMIT-1", "submit", order, NOW + timedelta(seconds=1)
    )
    request = make_request(commands=(command,))
    original_submit = lifecycle_api.submit

    def recording_submit(book, submitted):
        events.append("submit")
        return original_submit(book, submitted)

    monkeypatch.setattr(lifecycle_api, "submit", recording_submit)

    PaperOrderBookOrchestrator(runtime).execute(request)

    assert events == ["evaluate", "submit"]


def test_rejected_request_returns_runtime_result_unchanged() -> None:
    commands = (
        PaperOrderBookCommand(
            "DUP", "submit", make_order("ORDER-2"), NOW + timedelta(seconds=1)
        ),
        PaperOrderBookCommand(
            "DUP", "submit", make_order("ORDER-3"), NOW + timedelta(seconds=2)
        ),
    )
    request = make_request(
        commands=commands,
        identity=PaperOrderBookIdentity("REQUEST"),
        snapshot_identity=PaperOrderBookIdentity("OBSERVATION"),
    )
    runtime = RecordingRuntime([])
    book = request.snapshot.order_book
    before = book.history()

    result = PaperOrderBookOrchestrator(runtime).execute(request)

    assert result is runtime.result
    assert result.criteria.accepted is False
    assert book.history() == before


def test_submit_delegates_to_public_book_and_returns_new_observation() -> None:
    submitted = make_order("ORDER-2")
    command = PaperOrderBookCommand(
        "SUBMIT-1", "submit", submitted, NOW + timedelta(seconds=1)
    )
    request = make_request(commands=(command,))
    original_book = request.snapshot.order_book

    result = PaperOrderBookOrchestrator().execute(request)

    assert result.snapshot is not request.snapshot
    assert result.snapshot.order_book is original_book
    assert result.snapshot.identity is request.snapshot.identity
    assert result.snapshot.captured_at is request.completed_at
    assert original_book.history()[-1] is submitted
    assert result.commands is request.commands
    assert result.commands[0] is command


def test_accept_delegates_transition_through_public_facade(monkeypatch) -> None:
    order = make_order()
    command = PaperOrderBookCommand(
        "ACCEPT-1", "accept", order, NOW + timedelta(seconds=1)
    )
    request = make_request(commands=(command,))
    calls = []
    original_accept = lifecycle_api.accept

    def recording_accept(book, value, *, at=None):
        calls.append((book, value, at))
        return original_accept(book, value, at=at)

    monkeypatch.setattr(lifecycle_api, "accept", recording_accept)

    result = PaperOrderBookOrchestrator().execute(request)

    assert calls == [(request.snapshot.order_book, order, command.occurred_at)]
    assert result.snapshot.order_book.get(order.order_id).status is (
        lifecycle_api.OrderBookOrderStatus.ACCEPTED
    )


def test_equivalent_fresh_requests_execute_deterministically() -> None:
    first_request = make_request(
        commands=(
            PaperOrderBookCommand(
                "ACCEPT-1",
                "accept",
                make_order(),
                NOW + timedelta(seconds=1),
            ),
        )
    )
    second_request = make_request(
        commands=(
            PaperOrderBookCommand(
                "ACCEPT-1",
                "accept",
                make_order(),
                NOW + timedelta(seconds=1),
            ),
        )
    )

    first = PaperOrderBookOrchestrator().execute(first_request)
    second = PaperOrderBookOrchestrator().execute(second_request)

    assert first.snapshot.order_book.history() == second.snapshot.order_book.history()
    assert first.commands == second.commands
    assert first.summary == second.summary
    assert first.criteria == second.criteria
