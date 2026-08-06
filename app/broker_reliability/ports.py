from __future__ import annotations

from typing import Protocol

from app.broker_protocol.models import BrokerOrder, BrokerOrderRequest, BrokerPosition


class RecoveryBroker(Protocol):
    def submit_order(self, order: BrokerOrderRequest) -> BrokerOrder: ...

    def cancel_order(self, client_order_id: str) -> BrokerOrder: ...

    def replace_order(self, client_order_id: str, order: BrokerOrderRequest) -> BrokerOrder: ...

    def get_orders(self) -> tuple[BrokerOrder, ...]: ...

    def get_positions(self) -> tuple[BrokerPosition, ...]: ...
