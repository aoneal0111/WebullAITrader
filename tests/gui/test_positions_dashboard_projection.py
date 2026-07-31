from datetime import datetime, timezone

from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsPosition,
    PositionsUpdated,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_position(
    *,
    account_id: str = "account-1",
    symbol: str = "AAPL",
    quantity: str = "10",
    average_cost: str = "185.25",
    unrealized_gain_loss: str = "47.50",
    currency: str = "USD",
) -> OperationsPosition:
    return OperationsPosition(
        account_id=account_id,
        symbol=symbol,
        asset_type="EQUITY",
        quantity=quantity,
        average_cost=average_cost,
        market_value="1900.00",
        unrealized_gain_loss=unrealized_gain_loss,
        realized_gain_loss=None,
        currency=currency,
        updated_at=NOW,
    )


def test_positions_event_flows_to_dashboard_snapshot() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        bus.publish(
            PositionsUpdated(
                source="positions-runtime",
                positions=(
                    make_position(),
                    make_position(
                        account_id="account-2",
                        symbol="MSFT",
                        quantity="5",
                        average_cost="410",
                        unrealized_gain_loss="-25",
                    ),
                ),
                occurred_at=NOW,
            )
        )

        dashboard = project_dashboard(store.snapshot())

        assert dashboard.positions.rows == (
                (
                    "AAPL",
                    "LONG",
                    "10",
                    "$185.25",
                    "$190.00",
                    "+$47.50",
                    "+2.56%",
                ),
                (
                    "MSFT",
                    "LONG",
                    "5",
                    "$410.00",
                    "$380.00",
                    "-$25.00",
                    "-1.22%",
                ),
        )
    finally:
        store.close()


def test_cleared_positions_flow_to_empty_dashboard_snapshot() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        bus.publish(
            PositionsUpdated(
                positions=(make_position(),),
                occurred_at=NOW,
            )
        )
        bus.publish(
            PositionsUpdated(
                positions=(),
                occurred_at=NOW,
            )
        )

        dashboard = project_dashboard(store.snapshot())

        assert dashboard.positions.rows == ()
    finally:
        store.close()
