from __future__ import annotations
from app.authorization.validation import canonical_json
import json
from datetime import datetime
from decimal import Decimal
from app.authorization.models import LiveExecutionAuthorization
from app.broker_protocol.models import BrokerOrderType,BrokerSide,TimeInForce
def authorization_to_json(value)->str:return canonical_json(value)
def authorization_to_text(value)->str:return "LIVE AUTHORIZATION EVIDENCE — EXPLICIT CONSUMPTION REQUIRED\n"+canonical_json(value)+"\n"
def authorization_from_json(payload):
    try:value=json.loads(payload)
    except json.JSONDecodeError as exc:raise ValueError("authorization JSON is malformed") from exc
    if value.get("schema_version")!=2:raise ValueError("legacy authorization lacks required evidence and cannot be migrated")
    try:
        return LiveExecutionAuthorization(value["authorization_id"],value["intent_id"],value["intent_digest"],
            value["risk_approval_id"],value["risk_approval_digest"],value["compliance_approval_id"],
            value["compliance_approval_digest"],value["account_id"],value["symbol"],BrokerSide(value["side"]),
            BrokerOrderType(value["order_type"]),Decimal(value["quantity"]),_decimal(value.get("limit_price")),
            _decimal(value.get("stop_price")),TimeInForce(value["time_in_force"]),_dt(value["issued_at"]),
            _dt(value["expires_at"]),bool(value["single_use"]),2)
    except (KeyError,TypeError,ValueError) as exc:raise ValueError("authorization JSON is malformed") from exc
def _decimal(value):return None if value is None else Decimal(value)
def _dt(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise ValueError("authorization timestamp is naive")
    return result
