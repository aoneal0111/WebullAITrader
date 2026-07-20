from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.broker_protocol.models import BrokerOrderType,BrokerSide,TimeInForce

@dataclass(frozen=True,slots=True)
class ExecutionIntent:
    intent_id:str; account_id:str; symbol:str; side:BrokerSide; order_type:BrokerOrderType; quantity:Decimal
    limit_price:Decimal|None; stop_price:Decimal|None; time_in_force:TimeInForce; created_at:datetime
@dataclass(frozen=True,slots=True)
class RiskApprovalEvidence:
    approval_id:str; intent_id:str; intent_digest:str; approved:bool; account_id:str; symbol:str; side:BrokerSide
    order_type:BrokerOrderType; quantity:Decimal; limit_price:Decimal|None; stop_price:Decimal|None
    time_in_force:TimeInForce; issued_at:datetime; expires_at:datetime; revoked:bool=False; superseded:bool=False
@dataclass(frozen=True,slots=True)
class ComplianceApprovalEvidence:
    approval_id:str; intent_id:str; intent_digest:str; approved:bool; account_id:str; symbol:str; side:BrokerSide
    order_type:BrokerOrderType; quantity:Decimal; limit_price:Decimal|None; stop_price:Decimal|None
    time_in_force:TimeInForce; issued_at:datetime; expires_at:datetime; revoked:bool=False; superseded:bool=False
@dataclass(frozen=True,slots=True)
class LiveExecutionAuthorization:
    authorization_id:str; intent_id:str; intent_digest:str; risk_approval_id:str; risk_approval_digest:str
    compliance_approval_id:str; compliance_approval_digest:str; account_id:str; symbol:str; side:BrokerSide
    order_type:BrokerOrderType; quantity:Decimal; limit_price:Decimal|None; stop_price:Decimal|None
    time_in_force:TimeInForce; issued_at:datetime; expires_at:datetime; single_use:bool; schema_version:int=2
@dataclass(frozen=True,slots=True)
class ValidatedExecutionIntent:
    intent:ExecutionIntent
    authorization:LiveExecutionAuthorization
    def __post_init__(self):
        from app.authorization.validation import validate_authorization_binding
        validate_authorization_binding(self.intent,self.authorization)
    @property
    def request_id(self):return self.intent.intent_id
    @property
    def symbol(self):return self.intent.symbol
    @property
    def side(self):return self.intent.side
    @property
    def order_type(self):return self.intent.order_type
    @property
    def quantity(self):return self.intent.quantity
    @property
    def limit_price(self):return self.intent.limit_price
    @property
    def stop_price(self):return self.intent.stop_price
    @property
    def time_in_force(self):return self.intent.time_in_force
    @property
    def created_timestamp(self):return self.intent.created_at
