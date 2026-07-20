from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.authorization.models import ValidatedExecutionIntent
from app.authorization.validation import validate_authorization_binding
from app.broker_protocol.models import BrokerOrderRequest,BrokerOrderType as LiveOrderType,BrokerSide as LiveSide,TimeInForce
from app.broker_protocol.validation import validate_order_request

ZERO = Decimal(0)


def translate_order(intent: ValidatedExecutionIntent, current_timestamp: datetime) -> BrokerOrderRequest:
    if not isinstance(intent, ValidatedExecutionIntent): raise ValueError("ValidatedExecutionIntent is required")
    if current_timestamp.tzinfo is None: raise ValueError("current timestamp must be timezone-aware")
    authorization = intent.authorization;validate_authorization_binding(intent.intent,authorization)
    if authorization.issued_at > current_timestamp or current_timestamp >= authorization.expires_at: raise ValueError("live authorization is not currently valid")
    if intent.created_timestamp.tzinfo is None or intent.created_timestamp > current_timestamp: raise ValueError("intent timestamp is invalid")
    request = BrokerOrderRequest(intent.request_id, intent.symbol.upper(), intent.side, intent.order_type,
                                 intent.quantity, intent.limit_price, intent.stop_price, intent.time_in_force)
    validate_broker_order_request(request)
    return request


def validate_broker_order_request(request: BrokerOrderRequest) -> None:
    validate_order_request(request)


def _positive(value, name):
    if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO: raise ValueError(f"{name} must be a finite positive Decimal")
