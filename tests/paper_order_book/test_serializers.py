from datetime import timedelta

import pytest

from app.paper_order_book import (
    PaperOrderBookCriteriaResult,
    PaperOrderBookResult,
    PaperOrderBookSerializationError,
    PaperOrderBookSummary,
    serialize_command,
    serialize_request,
    serialize_result,
    serialize_snapshot,
)
from app.paper_trading.order_book_api import serializers as lifecycle_serializers
from tests.paper_order_book.helpers import make_request


def test_serialization_is_deterministic_and_preserves_command_order() -> None:
    request = make_request()

    first = serialize_request(request)
    second = serialize_request(request)

    assert first == second
    assert first["identity"] == {"order_book_id": "BOOK-1"}
    assert first["commands"][0]["command_id"] == "COMMAND-1"
    assert first["commands"][0]["payload"]["type"] == "order"
    assert first["requested_at"] == request.requested_at.isoformat()


def test_snapshot_and_command_delegate_to_lifecycle_serializers(monkeypatch) -> None:
    request = make_request()
    book_marker = {"delegated": "book"}
    order_marker = {"delegated": "order"}
    monkeypatch.setattr(
        lifecycle_serializers,
        "serialize_order_book",
        lambda value: book_marker,
    )
    monkeypatch.setattr(
        lifecycle_serializers,
        "serialize_order_book_order",
        lambda value: order_marker,
    )

    assert serialize_snapshot(request.snapshot)["order_book"] is book_marker
    assert serialize_command(request.commands[0])["payload"]["value"] is (
        order_marker
    )


def test_result_serializer_contains_application_models_only() -> None:
    request = make_request()
    criteria = PaperOrderBookCriteriaResult(True)
    result = PaperOrderBookResult(
        request.identity,
        request.snapshot,
        request.commands,
        PaperOrderBookSummary(1, 1),
        criteria,
        request.requested_at,
        request.completed_at,
    )

    data = serialize_result(result)

    assert data["summary"] == {"initial_orders": 1, "command_count": 1}
    assert data["criteria"] == {"accepted": True, "errors": []}
    assert data["errors"] == []


@pytest.mark.parametrize(
    ("serializer", "value"),
    [
        (serialize_request, {}),
        (serialize_snapshot, None),
        (serialize_command, "command"),
        (serialize_result, object()),
    ],
)
def test_serializers_reject_unsupported_types(serializer, value) -> None:
    with pytest.raises(PaperOrderBookSerializationError):
        serializer(value)
