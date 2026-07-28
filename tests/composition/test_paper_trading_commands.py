from decimal import Decimal

from app.paper_trading.command_composition import (
    PAPER_ACCOUNT_ID,
    PAPER_SESSION_ID,
    create_paper_trading_command_composition,
)
from app.services import OrderEntryCommand
from app.session.models import SessionStatus


def test_paper_trading_command_composition_places_order_in_shared_book() -> None:
    composition = create_paper_trading_command_composition()

    request = composition.order_command_factory.create_placement_request(
        OrderEntryCommand(
            symbol="aapl",
            side="BUY",
            quantity=Decimal("2"),
            order_type="MARKET",
            limit_price=None,
            stop_price=None,
            time_in_force="DAY",
        )
    )
    result = composition.trading_service.place_order(request)

    assert result.success is True
    assert composition.session_manager.state().status is SessionStatus.ACTIVE
    assert request.session_id == PAPER_SESSION_ID
    assert request.order.account_id == PAPER_ACCOUNT_ID
    assert composition.gateway.order_book is composition.order_book
    assert composition.order_book.get(result.broker_order_id).order_id == result.broker_order_id


def test_paper_trading_command_composition_cancels_same_order() -> None:
    from app.order_cancellation import OrderCancellationRequest

    composition = create_paper_trading_command_composition()
    request = composition.order_command_factory.create_placement_request(
        OrderEntryCommand(
            symbol="MSFT",
            side="SELL",
            quantity=Decimal("1"),
            order_type="LIMIT",
            limit_price=Decimal("500"),
            stop_price=None,
            time_in_force="GTC",
        )
    )
    placement = composition.trading_service.place_order(request)

    cancellation = composition.trading_service.cancel_order(
        OrderCancellationRequest(
            request_id="cancel-request-1",
            session_id=PAPER_SESSION_ID,
            account_id=PAPER_ACCOUNT_ID,
            broker_order_id=placement.broker_order_id,
            client_order_id=placement.client_order_id,
        )
    )

    assert cancellation.success is True
    assert composition.order_book.get(placement.broker_order_id).status.value == "CANCELLED"
