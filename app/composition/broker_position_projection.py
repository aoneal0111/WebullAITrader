from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.broker_protocol.models import BrokerAccount, BrokerCash, BrokerPosition
from app.composition.operations_position_mapper import map_broker_positions
from app.operations_core import (
    OperationsBus,
    OperationsPosition,
    PositionsUpdated,
)


BrokerPositionsSink = Callable[
    [tuple[BrokerPosition, ...], BrokerAccount, BrokerCash, datetime],
    tuple[OperationsPosition, ...],
]


def create_broker_positions_publisher(
    bus: OperationsBus,
    *,
    source: str = "live-broker",
) -> BrokerPositionsSink:
    """Create a publisher that projects live broker positions onto OperationsBus."""
    if not isinstance(bus, OperationsBus):
        raise TypeError("bus must be an OperationsBus")

    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source must not be empty")

    def publish_positions(
        positions: tuple[BrokerPosition, ...],
        account: BrokerAccount,
        cash: BrokerCash,
        occurred_at: datetime,
    ) -> tuple[OperationsPosition, ...]:
        if not isinstance(account, BrokerAccount):
            raise TypeError("account must be a BrokerAccount")
        if not isinstance(cash, BrokerCash):
            raise TypeError("cash must be a BrokerCash")

        mapped = map_broker_positions(
            positions,
            account_id=account.account_id_redacted,
            currency=cash.currency,
            updated_at=occurred_at,
        )
        bus.publish(
            PositionsUpdated(
                source=normalized_source,
                positions=mapped,
                occurred_at=occurred_at,
            )
        )
        return mapped

    return publish_positions


__all__ = [
    "BrokerPositionsSink",
    "create_broker_positions_publisher",
]
