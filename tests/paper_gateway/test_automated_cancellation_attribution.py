"""Regression coverage for automated cancellation attribution in PAPER gateway."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.order_cancellation import OrderCancellationRequest
from app.order_placement import NormalizedOrderStatus
from app.paper_gateway import PaperOrderGateway
from app.paper_gateway.gateway import _cancellation_reason
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderStatus, OrderTerminalReason
from app.services import OrderCommandFactory, OrderEntryCommand


NOW = datetime(2026, 10, 9, 16, 42, tzinfo=UTC)


def cancel_request(source=None, *, order_id="PAPER-TEST", client_order_id="client-test"):
    return OrderCancellationRequest(
        request_id="cancel-test",
        session_id="session-test",
        account_id="paper-account",
        broker_order_id=order_id,
        client_order_id=client_order_id,
        metadata={} if source is None else {"source": source},
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("autonomous-paper-correlated-exit", OrderTerminalReason.SYSTEM_CANCELLED),
        ("autonomous-paper-protective-replace", OrderTerminalReason.PROTECTIVE_REPLACED),
        ("autonomous-paper-entry-replace", OrderTerminalReason.ENTRY_REPLACED),
        (None, OrderTerminalReason.OPERATOR_CANCELLED),
        ("unknown-cancellation-source", OrderTerminalReason.OPERATOR_CANCELLED),
    ],
)
def test_cancel_source_classification(source, expected):
    assert _cancellation_reason(cancel_request(source)) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("autonomous-paper-correlated-exit", OrderTerminalReason.SYSTEM_CANCELLED),
        ("autonomous-paper-protective-replace", OrderTerminalReason.PROTECTIVE_REPLACED),
        (None, OrderTerminalReason.OPERATOR_CANCELLED),
    ],
)
def test_gateway_persists_terminal_reason_for_actual_cancellation(source, expected):
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book, clock=lambda: NOW)
    factory = OrderCommandFactory(
        session_id_provider=lambda: "session-test",
        account_id_provider=lambda: "paper-account",
        request_id_factory=lambda: "request-test",
        client_order_id_factory=lambda: "client-test",
    )
    placement = factory.create_placement_request(
        OrderEntryCommand(
            symbol="AAPL",
            side="BUY",
            quantity=Decimal("10"),
            order_type="LIMIT",
            limit_price=Decimal("100"),
            stop_price=None,
            time_in_force="DAY",
        )
    )
    acknowledgement = gateway.place_order(placement)
    assert acknowledgement.accepted
    cancelled = gateway.cancel_order(
        cancel_request(source, order_id=acknowledgement.broker_order_id)
    )
    assert cancelled is not None and cancelled.accepted
    stored = book.get(acknowledgement.broker_order_id)
    assert stored.status is OrderStatus.CANCELLED
    assert stored.terminal_reason is expected
