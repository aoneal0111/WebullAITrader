from __future__ import annotations
from datetime import datetime, timezone
from app.broker_protocol.models import BrokerAccount,BrokerOrder,BrokerOrderStatus as LiveOrderStatus
from app.webull.configuration import validate_configuration
from app.webull.errors import WebullTransportError, map_error
from app.webull.health import ConnectionHealth, update_health
from app.webull.serializers import order_request_payload, parse_cash, parse_fill, parse_order, parse_position

class WebullBrokerTransport:
    def __init__(self, configuration, http_client, auth, logger, clock=lambda: datetime.now(timezone.utc)):
        self.configuration = validate_configuration(configuration); self.http = http_client; self.auth = auth
        self.logger, self.clock = logger, clock; self.health = ConnectionHealth(); self._known = {}; self.__mutation_capability = None
    def bind_mutation_capability(self, capability):
        from app.broker_protocol.capability import BrokerMutationCapability
        if not isinstance(capability, BrokerMutationCapability): raise PermissionError("valid execution mutation capability is required")
        if self.__mutation_capability is not None and self.__mutation_capability is not capability: raise PermissionError("transport mutation capability is already bound")
        self.__mutation_capability = capability
    def connect(self):
        try:
            if not self.auth.verify():
                raise ValueError("connection verification failed")

            values = _items(
                self.http.get("/openapi/account/list")
            )

            account_found = any(
                str(item.get("account_id", "")).strip()
                == self.configuration.account_id.strip()
                for item in values
            )

            if not account_found:
                raise ValueError("configured account not found")

            self.health = update_health(
                self.health,
                connected=True,
                authenticated=True,
            )
            self.logger.log("connect", "succeeded")

        except Exception as exc:
            self.logger.log("connect", "failed")
            raise map_error(exc)
            
    def disconnect(self): self.health = update_health(self.health, connected=False, authenticated=False); self.logger.log("disconnect", "succeeded")
    def dispatch_submit(self, capability, order):
        self._authorize_mutation(capability); self._connected(); result = parse_order(self.http.post("/openapi/trade/order/place", payload=order_request_payload(order, self.configuration.account_id, self.clock())), order); self._known[order.client_order_id] = result; return result
    def dispatch_cancel(self, capability, client_order_id):
        self._authorize_mutation(capability); self._connected(); value = self.http.post("/openapi/trade/order/cancel", payload={"account_id": self.configuration.account_id, "client_order_id": client_order_id})
        known = self._known.get(client_order_id)
        if known is None: raise map_error(ValueError("unknown local broker order"))
        result = BrokerOrder(str(value.get("order_id") or value.get("broker_order_id") or known.broker_order_id), known.client_order_id,
            known.symbol, known.side, known.order_type, known.quantity, known.filled_quantity, known.limit_price,
            known.stop_price, known.time_in_force, LiveOrderStatus.CANCELLED, self.clock())
        self._known[client_order_id] = result; return result
    def dispatch_replace(self, capability, client_order_id, order):
        self._authorize_mutation(capability); self._connected(); result = parse_order(self.http.post("/openapi/trade/order/replace", payload=order_request_payload(order, self.configuration.account_id, self.clock())), order); self._known[client_order_id] = result; return result
    def get_positions(self):
        self._connected(); values = self.http.get("/openapi/assets/positions", query={"account_id": self.configuration.account_id}); return tuple(sorted((parse_position(item) for item in _items(values)), key=lambda item: item.symbol))
    def get_orders(self):
        self._connected(); values = self.http.get("/openapi/trade/order/open", query={"account_id": self.configuration.account_id, "page_size": "100"}); result = tuple(sorted((parse_order(item) for item in _items(values)), key=lambda item: item.client_order_id)); self._known.update((item.client_order_id, item) for item in result); return result
    def get_cash(self):
        self._connected(); return parse_cash(self.http.get("/openapi/assets/balance", query={"account_id": self.configuration.account_id}))
    def get_account(self):
        self._connected(); values = _items(self.http.get("/openapi/account/list")); item = next((item for item in values if str(item.get("account_id")) == self.configuration.account_id), None)
        if item is None: raise map_error(ValueError("configured account not found"))
        return BrokerAccount(_redact(self.configuration.account_id), str(item.get("account_type", item.get("account_class", "UNKNOWN"))), str(item.get("status", "UNKNOWN")))
    def get_fills(self):
        self._connected(); values = self.http.get("/openapi/trade/order/history", query={"account_id": self.configuration.account_id}); return tuple(sorted((parse_fill(item) for item in _items(values) if item.get("fill_id")), key=lambda item: (item.timestamp, item.fill_id)))
    def _connected(self):
        if not self.health.connected or not self.health.authenticated: raise map_error(ValueError("Webull transport is not connected"))
    def _authorize_mutation(self, capability):
        if self.__mutation_capability is None or capability is not self.__mutation_capability: raise PermissionError("execution mutation capability is required")
def _items(value):
    if isinstance(value, list): return value
    if isinstance(value, dict):
        for key in ("data", "items", "orders", "positions", "accounts", "result"):
            if isinstance(value.get(key), list): return value[key]
    raise map_error(ValueError("expected broker list response"))
def _redact(value): return "*" * max(0, len(value) - 4) + value[-4:]

