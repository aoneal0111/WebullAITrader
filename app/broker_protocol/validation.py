from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from app.broker_protocol.models import *

ZERO=Decimal(0)
def validate_order_request(value:BrokerOrderRequest)->BrokerOrderRequest:
    if not isinstance(value,BrokerOrderRequest): raise ValueError("BrokerOrderRequest is required")
    if not value.client_order_id.strip() or not value.symbol.strip(): raise ValueError("order identity is required")
    if not isinstance(value.side,BrokerSide) or not isinstance(value.order_type,BrokerOrderType) or not isinstance(value.time_in_force,TimeInForce): raise ValueError("order enum is invalid")
    _positive(value.quantity,"quantity")
    for item,name in ((value.limit_price,"limit_price"),(value.stop_price,"stop_price")):
        if item is not None:_positive(item,name)
    if value.order_type is BrokerOrderType.MARKET and (value.limit_price is not None or value.stop_price is not None): raise ValueError("MARKET orders cannot contain prices")
    if value.order_type is BrokerOrderType.LIMIT and (value.limit_price is None or value.stop_price is not None): raise ValueError("LIMIT requires only limit_price")
    if value.order_type is BrokerOrderType.STOP and (value.stop_price is None or value.limit_price is not None): raise ValueError("STOP requires only stop_price")
    if value.order_type is BrokerOrderType.STOP_LIMIT and (value.stop_price is None or value.limit_price is None): raise ValueError("STOP_LIMIT requires both prices")
    return value
def validate_fill(value:BrokerFill)->BrokerFill:
    if not value.fill_id or not value.broker_order_id: raise ValueError("fill identity is required")
    _positive(value.quantity,"quantity");_positive(value.price,"price");_aware(value.timestamp);return value
def validate_broker_order(value:BrokerOrder)->BrokerOrder:
    if not value.broker_order_id or not value.client_order_id or not value.symbol:raise ValueError("broker order identity is required")
    _positive(value.quantity,"quantity")
    if not isinstance(value.filled_quantity,Decimal) or not value.filled_quantity.is_finite() or not ZERO<=value.filled_quantity<=value.quantity:raise ValueError("filled quantity is invalid")
    for item,name in ((value.limit_price,"limit_price"),(value.stop_price,"stop_price")):
        if item is not None:_positive(item,name)
    if not isinstance(value.status,BrokerOrderStatus):raise ValueError("broker order status is invalid")
    _aware(value.updated_timestamp);return value
def validate_position(value):
    if not value.symbol.strip():raise ValueError("position symbol is required")
    _finite(value.quantity,"quantity");_finite(value.average_price,"average_price")
    if value.average_price<ZERO:raise ValueError("average price must be nonnegative")
    if value.market_value is not None:_finite(value.market_value,"market_value")
    return value
def validate_cash(value):
    _finite(value.settled_cash,"settled_cash")
    if value.unsettled_cash is not None:_finite(value.unsettled_cash,"unsettled_cash")
    if value.buying_power is not None:_finite(value.buying_power,"buying_power")
    if value.equity is not None:_finite(value.equity,"equity")
    if not value.currency.strip():raise ValueError("currency is required")
    return value
def _positive(value,name):
    if not isinstance(value,Decimal) or not value.is_finite() or value<=ZERO: raise ValueError(f"{name} must be a finite positive Decimal")
def _finite(value,name):
    if not isinstance(value,Decimal) or not value.is_finite():raise ValueError(f"{name} must be a finite Decimal")
def _aware(value):
    if not isinstance(value,datetime) or value.tzinfo is None: raise ValueError("timestamp must be timezone-aware")
