"""Read-only broker account observation shared by operational runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.broker_protocol.models import (
    BrokerAccount,
    BrokerCash,
    BrokerOrder,
    BrokerPosition,
)
from app.broker_protocol.protocol import Broker


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    """One immutable, internally consistent broker polling result."""

    account: BrokerAccount
    cash: BrokerCash
    positions: tuple[BrokerPosition, ...]
    orders: tuple[BrokerOrder, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.account, BrokerAccount):
            raise TypeError("account must be a BrokerAccount")
        if not isinstance(self.cash, BrokerCash):
            raise TypeError("cash must be BrokerCash")
        if not isinstance(self.positions, tuple) or any(
            not isinstance(position, BrokerPosition)
            for position in self.positions
        ):
            raise TypeError("positions must be immutable BrokerPositions")
        if not isinstance(self.orders, tuple) or any(
            not isinstance(order, BrokerOrder)
            for order in self.orders
        ):
            raise TypeError("orders must be immutable BrokerOrders")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
        ):
            raise ValueError("observed_at must be timezone-aware")


def poll_broker_account(
    broker: Broker,
    *,
    clock: Callable[[], datetime],
) -> BrokerAccountSnapshot:
    """Execute the existing read-only operational account polling cycle."""

    if not callable(clock):
        raise TypeError("clock must be callable")

    return BrokerAccountSnapshot(
        account=broker.get_account(),
        cash=broker.get_cash(),
        positions=broker.get_positions(),
        orders=broker.get_orders(),
        observed_at=clock(),
    )


__all__ = ["BrokerAccountSnapshot", "poll_broker_account"]
