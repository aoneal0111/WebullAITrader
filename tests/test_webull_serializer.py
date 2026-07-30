from decimal import Decimal

from app.broker_protocol.models import (
    BrokerOrderRequest,
    BrokerOrderType,
    BrokerSide,
    TimeInForce,
    TradingSession,
)
from app.webull.serializers import order_request_payload, parse_cash


TEST_ACCOUNT_ID = "test-account"


def make_order(session: TradingSession) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="test-order",
        symbol="AAPL",
        side=BrokerSide.BUY,
        order_type=BrokerOrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("150.00"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        trading_session=session,
    )


def test_core_session():
    payload = order_request_payload(
        make_order(TradingSession.CORE),
        TEST_ACCOUNT_ID,
    )
    assert payload["new_orders"][0]["support_trading_session"] == "CORE"


def test_extended_session():
    payload = order_request_payload(
        make_order(TradingSession.EXTENDED),
        TEST_ACCOUNT_ID,
    )
    assert payload["new_orders"][0]["support_trading_session"] == "ALL"


def test_overnight_session():
    payload = order_request_payload(
        make_order(TradingSession.OVERNIGHT),
        TEST_ACCOUNT_ID,
    )
    assert payload["new_orders"][0]["support_trading_session"] == "NIGHT"


def test_balance_parser_preserves_account_buying_power_and_equity():
    cash = parse_cash(
        {
            "total_cash_balance": "8000",
            "unsettled_cash": "0",
            "buying_power": "9000",
            "net_liquidation": "10500",
            "currency": "USD",
        }
    )

    assert cash.settled_cash == Decimal("8000")
    assert cash.buying_power == Decimal("9000")
    assert cash.equity == Decimal("10500")
