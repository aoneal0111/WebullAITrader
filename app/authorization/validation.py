from __future__ import annotations
import hashlib,json
from dataclasses import fields,is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from app.authorization.models import *
from app.broker_protocol.models import BrokerOrderRequest
from app.broker_protocol.validation import validate_order_request

def canonical_json(value)->str:return json.dumps(_safe(value),sort_keys=True,separators=(",",":"))
def canonical_digest(value)->str:return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def intent_digest(intent:ExecutionIntent)->str:
    validate_intent(intent);return canonical_digest(intent)
def validate_intent(intent):
    if not isinstance(intent,ExecutionIntent):raise ValueError("ExecutionIntent is required")
    if not intent.intent_id.strip() or not intent.account_id.strip():raise ValueError("intent identity is required")
    _aware(intent.created_at)
    validate_order_request(BrokerOrderRequest(intent.intent_id,intent.symbol.upper(),intent.side,intent.order_type,intent.quantity,intent.limit_price,intent.stop_price,intent.time_in_force))
    if intent.symbol!=intent.symbol.upper():raise ValueError("intent symbol must be normalized")
    return intent
def validate_evidence(intent,evidence,now):
    validate_intent(intent);_aware(now)
    if not evidence.approved:raise ValueError("approval evidence is not approved")
    if evidence.revoked or evidence.superseded:raise ValueError("approval evidence is revoked or superseded")
    _aware(evidence.issued_at);_aware(evidence.expires_at)
    if evidence.issued_at>now or now>=evidence.expires_at:raise ValueError("approval evidence is not currently valid")
    if evidence.intent_id!=intent.intent_id or evidence.intent_digest!=intent_digest(intent):raise ValueError("approval intent identity mismatch")
    _match(intent,evidence)
def validate_authorization_binding(intent,authorization):
    validate_intent(intent)
    if not isinstance(authorization,LiveExecutionAuthorization):raise ValueError("LiveExecutionAuthorization is required")
    if authorization.intent_id!=intent.intent_id or authorization.intent_digest!=intent_digest(intent):raise ValueError("authorization intent mismatch")
    _match(intent,authorization);_aware(authorization.issued_at);_aware(authorization.expires_at)
def validate_for_consumption(intent,authorization,now):
    validate_authorization_binding(intent,authorization);_aware(now)
    if authorization.issued_at>now or now>=authorization.expires_at:raise ValueError("authorization is expired or not active")
def _match(intent,evidence):
    for name in ("account_id","symbol","side","order_type","quantity","limit_price","stop_price","time_in_force"):
        if getattr(evidence,name)!=getattr(intent,name):raise ValueError(f"approval or authorization {name} mismatch")
def _safe(value):
    if isinstance(value,Decimal):
        if not value.is_finite():raise ValueError("non-finite Decimal")
        return format(value,"f")
    if isinstance(value,datetime):
        _aware(value);return value.isoformat()
    if isinstance(value,Enum):return value.value
    if is_dataclass(value) and not isinstance(value,type):return {field.name:_safe(getattr(value,field.name)) for field in fields(value)}
    if isinstance(value,(tuple,list)):return [_safe(item) for item in value]
    return value
def _aware(value):
    if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError("timestamps must be timezone-aware")
