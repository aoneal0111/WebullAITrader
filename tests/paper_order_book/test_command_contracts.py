from datetime import timedelta
from decimal import Decimal

import pytest

import app.paper_order_book as api
import app.paper_order_book.dispatcher as command_dispatcher
import app.paper_trading.order_book_api as lifecycle_api
from app.paper_order_book.command_contracts import (
    ACCEPT,
    APPLY_FILL,
    CANCEL,
    COMMAND_PAYLOAD_TYPES,
    EXPIRE,
    EXPIRE_DAY_ORDERS,
    REJECT,
    SUBMIT,
    SUPPORTED_COMMAND_TYPES,
    UPDATE,
)
from tests.paper_order_book.helpers import NOW, make_order, make_request


def payloads(book):
    order = make_order("ORDER-2")
    return {
        SUBMIT: order,
        UPDATE: order,
        CANCEL: order,
        ACCEPT: order,
        REJECT: api.PaperOrderBookRejection(
            order=order,
            reason="Risk limit exceeded",
        ),
        EXPIRE: order,
        APPLY_FILL: api.create_fill(
            fill_id="FILL-1",
            order_id="ORDER-1",
            quantity=Decimal("1"),
            price=Decimal("100"),
            occurred_at=NOW + timedelta(seconds=1),
        ),
        EXPIRE_DAY_ORDERS: book,
    }


def test_supported_command_contract_is_complete_and_immutable() -> None:
    assert SUPPORTED_COMMAND_TYPES == (
        "submit",
        "update",
        "cancel",
        "accept",
        "reject",
        "expire",
        "apply_fill",
        "expire_day_orders",
    )
    with pytest.raises(TypeError):
        COMMAND_PAYLOAD_TYPES["other"] = object


def test_validation_accepts_every_authoritative_command_contract() -> None:
    book = make_request().snapshot.order_book
    for index, (command_type, payload) in enumerate(payloads(book).items()):
        command = api.PaperOrderBookCommand(
            command_id=f"COMMAND-{index}",
            command_type=command_type,
            payload=payload,
            occurred_at=NOW + timedelta(seconds=1),
        )
        request = make_request(commands=(command,))

        assert api.validate_request(request).accepted is True


@pytest.mark.parametrize(
    ("command_type", "operation"),
    [
        (SUBMIT, "submit"),
        (UPDATE, "update"),
        (CANCEL, "cancel"),
        (ACCEPT, "accept"),
        (REJECT, "reject"),
        (EXPIRE, "expire"),
        (APPLY_FILL, "record_fill"),
        (EXPIRE_DAY_ORDERS, "expire_day_orders"),
    ],
)
def test_dispatcher_routes_every_authoritative_command(
    monkeypatch, command_type, operation
) -> None:
    book = make_request().snapshot.order_book
    payload = payloads(book)[command_type]
    command = api.PaperOrderBookCommand(
        command_id="COMMAND-1",
        command_type=command_type,
        payload=payload,
        occurred_at=NOW + timedelta(seconds=1),
    )
    calls = []

    def recording_operation(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(lifecycle_api, operation, recording_operation)

    command_dispatcher.dispatch_command(book, command)

    assert len(calls) == 1
    assert calls[0][0][0] is book


def test_all_public_command_factories_remain_compatible() -> None:
    order = make_order("ORDER-2")
    timestamp = NOW + timedelta(seconds=1)
    fill = api.create_fill(
        fill_id="FILL-1",
        order_id="ORDER-1",
        quantity=Decimal("1"),
        price=Decimal("100"),
        occurred_at=timestamp,
    )
    commands = (
        api.create_submit_command(
            command_id="SUBMIT-1",
            order_id="ORDER-2",
            occurred_at=timestamp,
            symbol="AAPL",
            asset_class="STOCK",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("1"),
            time_in_force="DAY",
        ),
        api.create_update_command(
            command_id="UPDATE-1",
            order=order,
            occurred_at=timestamp,
        ),
        api.create_cancel_command(
            command_id="CANCEL-1",
            order=order,
            occurred_at=timestamp,
        ),
        api.create_accept_command(
            command_id="ACCEPT-1",
            order=order,
            occurred_at=timestamp,
        ),
        api.create_reject_command(
            command_id="REJECT-1",
            order=order,
            reason="Risk limit exceeded",
            occurred_at=timestamp,
        ),
        api.create_expire_command(
            command_id="EXPIRE-1",
            order=order,
            occurred_at=timestamp,
        ),
        api.create_apply_fill_command(
            command_id="FILL-1",
            fill=fill,
            occurred_at=timestamp,
        ),
    )

    assert {command.command_type for command in commands} == (
        set(SUPPORTED_COMMAND_TYPES) - {EXPIRE_DAY_ORDERS}
    )
    for command in commands:
        assert isinstance(
            command.payload,
            COMMAND_PAYLOAD_TYPES[command.command_type],
        )
        assert api.validate_request(
            make_request(commands=(command,))
        ).accepted is True
