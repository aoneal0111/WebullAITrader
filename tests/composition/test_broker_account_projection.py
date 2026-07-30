from datetime import UTC, datetime
from decimal import Decimal

from app.broker_protocol.models import (
    BrokerAccount,
    BrokerCash,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
    BrokerSide,
    TimeInForce,
)
from app.composition.broker_account_projection import (
    create_broker_account_publisher,
)
from app.live_execution.account_polling import BrokerAccountSnapshot
from app.operations_core import (
    ApplicationStateStore,
    BrokerAccountUpdated,
    OperationsBus,
)


NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


def test_broker_account_publication_updates_complete_application_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    account_events: list[BrokerAccountUpdated] = []
    bus.subscribe(BrokerAccountUpdated, account_events.append)
    publish = create_broker_account_publisher(bus)
    snapshot = BrokerAccountSnapshot(
        account=BrokerAccount(
            account_id_redacted="******ount",
            account_type="CASH",
            status="ACTIVE",
        ),
        cash=BrokerCash(
            settled_cash=Decimal("8000"),
            unsettled_cash=Decimal("0"),
            currency="USD",
            buying_power=Decimal("9000"),
            equity=Decimal("10500"),
        ),
        positions=(
            BrokerPosition(
                symbol="AAPL",
                quantity=Decimal("10"),
                average_price=Decimal("100"),
                market_value=Decimal("1100"),
            ),
        ),
        orders=(
            BrokerOrder(
                broker_order_id="broker-1",
                client_order_id="client-1",
                symbol="AAPL",
                side=BrokerSide.BUY,
                order_type=BrokerOrderType.LIMIT,
                quantity=Decimal("2"),
                filled_quantity=Decimal("0"),
                limit_price=Decimal("95"),
                stop_price=None,
                time_in_force=TimeInForce.DAY,
                status=BrokerOrderStatus.ACKNOWLEDGED,
                updated_timestamp=NOW,
            ),
        ),
        observed_at=NOW,
    )

    try:
        publish(snapshot)
        state = store.snapshot()

        assert len(account_events) == 1
        assert state.broker_account is account_events[0].account
        assert state.broker_account.buying_power == Decimal("9000")
        assert state.broker_account.cash_balance == Decimal("8000")
        assert state.broker_account.equity == Decimal("10500")
        assert state.positions[0].symbol == "AAPL"
        assert state.orders[0].order_id == "broker-1"
        assert state.position_projection.positions[0].symbol == "AAPL"
        assert state.order_projection.orders[0].order_id == "broker-1"
        assert state.portfolio_projection.open_positions == 1
        assert state.portfolio_projection.working_orders == 1
    finally:
        store.close()
