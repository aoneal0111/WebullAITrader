"""Publish a complete broker account observation to desktop state."""

from __future__ import annotations

from collections.abc import Callable

from app.account_information.models import BrokerNeutralAccountInformation
from app.composition.broker_order_projection import (
    create_broker_orders_publisher,
)
from app.composition.broker_position_projection import (
    create_broker_positions_publisher,
)
from app.live_execution.account_polling import BrokerAccountSnapshot
from app.operations_core import (
    BrokerAccountUpdated,
    OperationsBus,
    PortfolioUpdated,
)
from app.read_models.orders.projector import project_operational_orders
from app.read_models.portfolio_projection import (
    aggregate_portfolio,
    to_operations_portfolio,
)
from app.read_models.positions.projector import project_operational_positions


BrokerAccountSnapshotSink = Callable[[BrokerAccountSnapshot], None]


def create_broker_account_publisher(
    bus: OperationsBus,
    *,
    source: str = "live-broker",
) -> BrokerAccountSnapshotSink:
    """Compose existing broker publishers into one account snapshot sink."""

    if not isinstance(bus, OperationsBus):
        raise TypeError("bus must be an OperationsBus")
    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source must not be empty")

    publish_positions = create_broker_positions_publisher(
        bus,
        source=normalized_source,
    )
    publish_orders = create_broker_orders_publisher(
        bus,
        source=normalized_source,
    )

    def publish(snapshot: BrokerAccountSnapshot) -> None:
        if not isinstance(snapshot, BrokerAccountSnapshot):
            raise TypeError("snapshot must be a BrokerAccountSnapshot")

        cash_balance = snapshot.cash.settled_cash
        market_value = sum(
            (
                position.market_value
                if position.market_value is not None
                else position.quantity * position.average_price
                for position in snapshot.positions
            ),
            start=cash_balance * 0,
        )
        account = BrokerNeutralAccountInformation(
            account_id=snapshot.account.account_id_redacted,
            account_type=snapshot.account.account_type,
            account_status=snapshot.account.status,
            buying_power=(
                snapshot.cash.buying_power
                if snapshot.cash.buying_power is not None
                else cash_balance
            ),
            cash_balance=cash_balance,
            equity=(
                snapshot.cash.equity
                if snapshot.cash.equity is not None
                else cash_balance + market_value
            ),
            currency=snapshot.cash.currency,
            metadata={"source": normalized_source},
        )
        bus.publish(
            BrokerAccountUpdated(
                source=normalized_source,
                occurred_at=snapshot.observed_at,
                account=account,
            )
        )

        positions = publish_positions(
            snapshot.positions,
            snapshot.account,
            snapshot.cash,
            snapshot.observed_at,
        )
        orders = publish_orders(snapshot.orders, snapshot.observed_at)
        portfolio = aggregate_portfolio(
            project_operational_positions(positions),
            project_operational_orders(orders),
        )
        bus.publish(
            PortfolioUpdated(
                source=normalized_source,
                occurred_at=snapshot.observed_at,
                summary=to_operations_portfolio(portfolio),
            )
        )

    return publish


__all__ = [
    "BrokerAccountSnapshotSink",
    "create_broker_account_publisher",
]
