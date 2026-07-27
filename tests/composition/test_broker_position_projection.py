from datetime import datetime, timezone
from decimal import Decimal

from app.broker_protocol.models import BrokerAccount, BrokerCash, BrokerPosition
from app.composition.broker_position_projection import create_broker_positions_publisher
from app.operations_core import ApplicationStateStore, OperationsBus


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_broker_positions_publisher_updates_application_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    publisher = create_broker_positions_publisher(bus)

    try:
        publisher(
            (
                BrokerPosition(
                    symbol="AAPL",
                    quantity=Decimal("10"),
                    average_price=Decimal("185.25"),
                    market_value=Decimal("1900"),
                ),
            ),
            BrokerAccount(
                account_id_redacted="acct-redacted",
                account_type="CASH",
                status="ACTIVE",
            ),
            BrokerCash(
                settled_cash=Decimal("1000"),
                unsettled_cash=None,
                currency="USD",
            ),
            NOW,
        )

        position = store.snapshot().positions[0]
        assert position.account_id == "acct-redacted"
        assert position.symbol == "AAPL"
        assert position.unrealized_gain_loss == "47.50"
        assert position.updated_at == NOW
    finally:
        store.close()
