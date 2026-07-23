import inspect

from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookObservation,
    PaperOrderBookPolicy,
    PaperOrderBookRequest,
    create_observation,
    create_request,
    serialize_request,
    validate_request,
)
from app.paper_trading.order_book_api import PaperOrderBook
from tests.paper_order_book.helpers import NOW, make_lifecycle_request


def test_create_observation_preserves_caller_values() -> None:
    identity = PaperOrderBookIdentity("BOOK-1")
    observation = create_observation(identity=identity, captured_at=NOW)

    assert isinstance(observation, PaperOrderBookObservation)
    assert observation.identity is identity
    assert observation.captured_at is NOW
    assert isinstance(observation.order_book, PaperOrderBook)
    assert len(observation.order_book) == 0


def test_each_observation_has_an_independent_empty_book() -> None:
    identity = PaperOrderBookIdentity("BOOK-1")
    first = create_observation(identity=identity, captured_at=NOW)
    second = create_observation(identity=identity, captured_at=NOW)

    assert first.order_book is not second.order_book
    assert first.order_book.history() == second.order_book.history() == ()


def test_create_request_preserves_all_caller_owned_values() -> None:
    identity = PaperOrderBookIdentity("BOOK-1")
    policy = PaperOrderBookPolicy()
    commands = (
        PaperOrderBookCommand(
            "COMMAND-1", "observe", make_lifecycle_request(), NOW
        ),
    )
    request = create_request(
        identity=identity,
        policy=policy,
        requested_at=NOW,
        completed_at=NOW,
        commands=commands,
    )

    assert isinstance(request, PaperOrderBookRequest)
    assert request.identity is identity
    assert request.policy is policy
    assert request.requested_at is NOW
    assert request.completed_at is NOW
    assert request.commands is commands
    assert request.snapshot.identity is identity
    assert request.snapshot.captured_at is NOW
    assert validate_request(request).accepted is True


def test_create_request_defaults_to_empty_commands() -> None:
    request = create_request(
        identity=PaperOrderBookIdentity("BOOK-1"),
        policy=PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
    )
    assert request.commands == ()


def test_equal_inputs_serialize_deterministically_with_independent_books() -> None:
    identity = PaperOrderBookIdentity("BOOK-1")
    policy = PaperOrderBookPolicy()
    first = create_request(
        identity=identity,
        policy=policy,
        requested_at=NOW,
        completed_at=NOW,
    )
    second = create_request(
        identity=identity,
        policy=policy,
        requested_at=NOW,
        completed_at=NOW,
    )

    assert first.snapshot.order_book is not second.snapshot.order_book
    assert serialize_request(first) == serialize_request(second)


def test_lifecycle_book_type_is_absent_from_public_factory_signatures() -> None:
    for factory in (create_observation, create_request):
        signature = inspect.signature(factory)
        annotations = [
            parameter.annotation
            for parameter in signature.parameters.values()
        ] + [signature.return_annotation]
        assert PaperOrderBook not in annotations
