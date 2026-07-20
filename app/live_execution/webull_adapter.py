from __future__ import annotations

from app.broker_protocol.protocol import Broker
from app.broker_protocol.models import BrokerOrderRequest
from app.broker_protocol.capability import _issue_broker_mutation_capability


class WebullAdapter:
    """Transport-isolated Webull broker adapter; contains no HTTP or MCP implementation."""

    requires_durable_journal = True

    def __init__(self, transport: Broker) -> None:
        self._transport = transport
        self._connected = False
        self.__mutation_capability = _issue_broker_mutation_capability()
        self.__capability_bound = hasattr(transport, "bind_mutation_capability")
        if self.__capability_bound: transport.bind_mutation_capability(self.__mutation_capability)

    def connect(self): self._transport.connect(); self._connected = True
    def disconnect(self): self._transport.disconnect(); self._connected = False
    def _require(self):
        if not self._connected: raise RuntimeError("Webull adapter is not connected")
    def submit_order(self, order: BrokerOrderRequest): self._require(); return self._transport.dispatch_submit(self.__mutation_capability, order) if self.__capability_bound else self._transport.submit_order(order)
    def cancel_order(self, client_order_id: str): self._require(); return self._transport.dispatch_cancel(self.__mutation_capability, client_order_id) if self.__capability_bound else self._transport.cancel_order(client_order_id)
    def replace_order(self, client_order_id: str, order: BrokerOrderRequest): self._require(); return self._transport.dispatch_replace(self.__mutation_capability, client_order_id, order) if self.__capability_bound else self._transport.replace_order(client_order_id, order)
    def get_positions(self): self._require(); return self._transport.get_positions()
    def get_orders(self): self._require(); return self._transport.get_orders()
    def get_cash(self): self._require(); return self._transport.get_cash()
    def get_account(self): self._require(); return self._transport.get_account()
    def get_fills(self): self._require(); return self._transport.get_fills()
