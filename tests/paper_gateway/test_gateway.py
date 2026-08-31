from app.order_cancellation import OrderCancellationRequest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.order_placement import NormalizedOrderStatus
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.services import OrderCommandFactory, OrderEntryCommand
from app.paper_gateway import PaperOrderGateway
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderStatus
from app.services.order_command_factory import OrderEntryCommand

from decimal import Decimal


NOW = datetime(2026, 8, 31, 11, 30, tzinfo=UTC)


def placement_request():
    factory = OrderCommandFactory(
        session_id_provider=lambda: "session-1",
        account_id_provider=lambda: "paper-account",
        request_id_factory=lambda: "request-1",
        client_order_id_factory=lambda: "client-1",
    )

    return factory.create_placement_request(
        OrderEntryCommand(
            symbol="AAPL",
            side="BUY",
            quantity=Decimal("10"),
            order_type="MARKET",
            limit_price=None,
            stop_price=None,
            time_in_force="DAY",
        )
    )


def test_places_order_into_shared_book() -> None:
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book)

    acknowledgement = gateway.place_order(
        placement_request()
    )

    assert acknowledgement.accepted is True
    assert (
        acknowledgement.status
        is NormalizedOrderStatus.SUBMITTED
    )
    assert acknowledgement.client_order_id == "client-1"

    stored = book.get(
        acknowledgement.broker_order_id
    )

    assert stored.status is OrderStatus.ACCEPTED
    assert stored.symbol == "AAPL"
    assert stored.request.client_order_id == "client-1"


def test_cancels_order_from_shared_book() -> None:
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book)

    placement = gateway.place_order(
        placement_request()
    )

    cancellation = gateway.cancel_order(
        OrderCancellationRequest(
            request_id="cancel-1",
            session_id="session-1",
            account_id="paper-account",
            broker_order_id=placement.broker_order_id,
            client_order_id="client-1",
        )
    )

    assert cancellation is not None
    assert cancellation.accepted is True
    assert (
        book.get(placement.broker_order_id).status
        is OrderStatus.CANCELLED
    )


def test_missing_order_returns_none() -> None:
    gateway = PaperOrderGateway(PaperOrderBook())

    result = gateway.cancel_order(
        OrderCancellationRequest(
            request_id="cancel-1",
            session_id="session-1",
            account_id="paper-account",
            broker_order_id="PAPER-MISSING",
            client_order_id="client-1",
        )
    )

    assert result is None


def test_client_order_id_mismatch_is_rejected() -> None:
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book)

    placement = gateway.place_order(
        placement_request()
    )

    cancellation = gateway.cancel_order(
        OrderCancellationRequest(
            request_id="cancel-1",
            session_id="session-1",
            account_id="paper-account",
            broker_order_id=placement.broker_order_id,
            client_order_id="wrong-client",
        )
    )

    assert cancellation is not None
    assert cancellation.accepted is False
    assert (
        book.get(placement.broker_order_id).status
        is OrderStatus.ACCEPTED
    )


def test_delayed_queued_quote_cannot_fill_working_paper_order() -> None:
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book, clock=lambda: NOW)
    placement = gateway.place_order(placement_request())
    delayed = MarketEvent(
        1,
        NOW - timedelta(minutes=10),
        "AAPL",
        "webull",
        MarketEventType.QUOTE,
        QuotePayload(
            Decimal("99"), Decimal("100"), Decimal("100"), Decimal("100")
        ),
        received_timestamp=NOW - timedelta(seconds=5, milliseconds=1),
    )

    assert gateway.process_market_event(delayed) == ()
    assert book.get(placement.broker_order_id).status is OrderStatus.ACCEPTED

    fresh = replace(
        delayed,
        sequence=2,
        timestamp=NOW,
        received_timestamp=NOW - timedelta(milliseconds=100),
    )
    reports = gateway.process_market_event(fresh)
    assert reports and reports[0].fills
    assert book.get(placement.broker_order_id).status is OrderStatus.FILLED


