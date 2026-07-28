from decimal import Decimal

import pytest

from app.order_placement import (
    OrderPlacementRequest,
    OrderSide,
    OrderType,
    TimeInForce,
)
from app.services.order_command_factory import (
    OrderCommandFactory,
    OrderEntryCommand,
)


def create_factory() -> OrderCommandFactory:
    return OrderCommandFactory(
        session_id_provider=lambda: "session-123",
        account_id_provider=lambda: "account-456",
        request_id_factory=lambda: "request-789",
        client_order_id_factory=lambda: "client-012",
    )


def test_create_market_order_request() -> None:
    factory = create_factory()

    request = factory.create_placement_request(
        OrderEntryCommand(
            symbol=" aapl ",
            side="BUY",
            quantity=Decimal("10"),
            order_type="MARKET",
            limit_price=None,
            stop_price=None,
            time_in_force="DAY",
        )
    )

    assert isinstance(request, OrderPlacementRequest)
    assert request.session_id == "session-123"
    assert request.metadata["source"] == "desktop_order_entry"

    order = request.order

    assert order.request_id == "request-789"
    assert order.client_order_id == "client-012"
    assert order.account_id == "account-456"
    assert order.symbol == "AAPL"
    assert order.side is OrderSide.BUY
    assert order.order_type is OrderType.MARKET
    assert order.quantity == Decimal("10")
    assert order.limit_price is None
    assert order.stop_price is None
    assert order.time_in_force is TimeInForce.DAY


def test_create_stop_limit_order_request() -> None:
    request = create_factory().create_placement_request(
        OrderEntryCommand(
            symbol="msft",
            side="SELL",
            quantity=Decimal("2.5"),
            order_type="STOP_LIMIT",
            limit_price=Decimal("410.25"),
            stop_price=Decimal("411.00"),
            time_in_force="GTC",
            metadata={"origin": "manual"},
        )
    )

    assert request.order.side is OrderSide.SELL
    assert request.order.order_type is OrderType.STOP_LIMIT
    assert request.order.limit_price == Decimal("410.25")
    assert request.order.stop_price == Decimal("411.00")
    assert request.order.time_in_force is TimeInForce.GTC
    assert request.order.metadata["origin"] == "manual"


def test_rejects_non_command_input() -> None:
    factory = create_factory()

    with pytest.raises(
        TypeError,
        match="command must be OrderEntryCommand",
    ):
        factory.create_placement_request(object())


@pytest.mark.parametrize(
    ("provider_name", "session_provider", "account_provider"),
    (
        (
            "session_id_provider",
            lambda: "",
            lambda: "account-456",
        ),
        (
            "account_id_provider",
            lambda: "session-123",
            lambda: " ",
        ),
    ),
)
def test_rejects_empty_context_identifiers(
    provider_name,
    session_provider,
    account_provider,
) -> None:
    factory = OrderCommandFactory(
        session_id_provider=session_provider,
        account_id_provider=account_provider,
        request_id_factory=lambda: "request-789",
        client_order_id_factory=lambda: "client-012",
    )

    command = OrderEntryCommand(
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("1"),
        order_type="MARKET",
        limit_price=None,
        stop_price=None,
        time_in_force="DAY",
    )

    with pytest.raises(ValueError, match=provider_name):
        factory.create_placement_request(command)


def test_domain_model_rejects_invalid_market_prices() -> None:
    factory = create_factory()

    command = OrderEntryCommand(
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("1"),
        order_type="MARKET",
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force="DAY",
    )

    with pytest.raises(
        Exception,
        match="market order cannot contain prices",
    ):
        factory.create_placement_request(command)
