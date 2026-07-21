from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.broker_protocol.models import (
    BrokerOrder,
    BrokerOrderStatus,
)

_OPEN_STATUSES = frozenset(
    {
        BrokerOrderStatus.NEW,
        BrokerOrderStatus.SUBMITTED,
        BrokerOrderStatus.ACKNOWLEDGED,
        BrokerOrderStatus.PARTIALLY_FILLED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderIndex:
    """
    Immutable lookup structure over broker orders.
    """

    by_broker_order_id: dict[str, BrokerOrder]
    by_client_order_id: dict[str, BrokerOrder]
    by_symbol: dict[str, tuple[BrokerOrder, ...]]

    @classmethod
    def build(cls, orders: list[BrokerOrder]) -> "OrderIndex":
        broker: dict[str, BrokerOrder] = {}
        client: dict[str, BrokerOrder] = {}
        symbols: dict[str, list[BrokerOrder]] = defaultdict(list)

        for order in orders:
            broker[order.broker_order_id] = order
            client[order.client_order_id] = order
            symbols[order.symbol].append(order)

        return cls(
            by_broker_order_id=broker,
            by_client_order_id=client,
            by_symbol={
                symbol: tuple(values)
                for symbol, values in symbols.items()
            },
        )

    @classmethod
    def empty(cls) -> "OrderIndex":
        return cls({}, {}, {})

    def broker_order(
        self,
        broker_order_id: str,
    ) -> BrokerOrder | None:
        return self.by_broker_order_id.get(broker_order_id)

    def client_order(
        self,
        client_order_id: str,
    ) -> BrokerOrder | None:
        return self.by_client_order_id.get(client_order_id)

    def orders_for_symbol(
        self,
        symbol: str,
    ) -> tuple[BrokerOrder, ...]:
        return self.by_symbol.get(symbol, ())

    def open_orders_for_symbol(
        self,
        symbol: str,
    ) -> tuple[BrokerOrder, ...]:
        return tuple(
            order
            for order in self.orders_for_symbol(symbol)
            if order.status in _OPEN_STATUSES
        )

    def has_open_order(
        self,
        symbol: str,
    ) -> bool:
        return bool(self.open_orders_for_symbol(symbol))