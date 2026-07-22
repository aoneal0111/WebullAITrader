from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping,Any
from app.broker_execution import BrokerExecutionAuthorization,ExecutionMode,SafetyDecision
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.paper_broker.policies import PaperBrokerPolicy
from app.trade_proposals.models import TradeDirection,aware_timestamp
from app.trade_proposals.policies import decimal_value

class PaperBrokerExecutionStatus(StrEnum): ACKNOWLEDGED="ACKNOWLEDGED";FILLED="FILLED";REJECTED="REJECTED";DUPLICATE="DUPLICATE"
class PaperBrokerRejectionReason(StrEnum):
    NONE="NONE";INVALID_AUTHORIZATION_TYPE="INVALID_AUTHORIZATION_TYPE";AUTHORIZATION_NOT_APPROVED="AUTHORIZATION_NOT_APPROVED";INVALID_QUANTITY="INVALID_QUANTITY";INVALID_ENTRY_PRICE="INVALID_ENTRY_PRICE";DUPLICATE_AUTHORIZATION="DUPLICATE_AUTHORIZATION";UNSUPPORTED_MODE="UNSUPPORTED_MODE";INVALID_TIMESTAMP="INVALID_TIMESTAMP"

@dataclass(frozen=True,slots=True)
class PaperBrokerState:
    timestamp:datetime;executed_authorization_ids:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
        if not isinstance(self.executed_authorization_ids,(tuple,list)) or any(not isinstance(x,str) or not x.strip() for x in self.executed_authorization_ids):raise ValueError("executed_authorization_ids must be ordered nonempty strings")
        ids=tuple(x.strip() for x in self.executed_authorization_ids)
        if len(ids)!=len(set(ids)):raise ValueError("executed_authorization_ids must be unique")
        object.__setattr__(self,"executed_authorization_ids",ids);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"timestamp":self.timestamp.isoformat(),"executed_authorization_ids":list(self.executed_authorization_ids),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(datetime.fromisoformat(v["timestamp"]),tuple(v.get("executed_authorization_ids",())),v.get("metadata",{}))
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize paper broker state") from e

@dataclass(frozen=True,slots=True)
class PaperBrokerExecutionRequest:
    authorization:BrokerExecutionAuthorization;timestamp:datetime;policy:PaperBrokerPolicy;state:PaperBrokerState;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.authorization,BrokerExecutionAuthorization):raise ValueError("authorization must be a BrokerExecutionAuthorization")
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
        if self.timestamp<self.authorization.timestamp:raise ValueError("request timestamp cannot precede authorization timestamp")
        if not isinstance(self.policy,PaperBrokerPolicy):raise ValueError("policy must be PaperBrokerPolicy")
        if not isinstance(self.state,PaperBrokerState):raise ValueError("state must be PaperBrokerState")
        if self.state.timestamp>self.timestamp:raise ValueError("state timestamp cannot exceed request timestamp")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"authorization":self.authorization.to_dict(),"timestamp":self.timestamp.isoformat(),"policy":self.policy.to_dict(),"state":self.state.to_dict(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(BrokerExecutionAuthorization.from_dict(v["authorization"]),datetime.fromisoformat(v["timestamp"]),PaperBrokerPolicy.from_dict(v["policy"]),PaperBrokerState.from_dict(v["state"]),v.get("metadata",{}))
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize paper broker request") from e

@dataclass(frozen=True,slots=True)
class PaperBrokerExecutionResult:
    execution_id:str;authorization_id:str;proposal_id:str;request_fingerprint:str;symbol:str;direction:TradeDirection|None;quantity_requested:int;quantity_filled:int;entry_price:Decimal;fill_price:Decimal;filled_notional:Decimal;mode:ExecutionMode;timestamp:datetime;status:PaperBrokerExecutionStatus;rejection_reason:PaperBrokerRejectionReason;policy_version:str;adapter_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("execution_id","authorization_id","proposal_id","request_fingerprint","symbol","policy_version","adapter_version"):
            if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
        for n in ("quantity_requested","quantity_filled"):
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise ValueError(f"{n} must be nonnegative integer")
        for n in ("entry_price","fill_price","filled_notional"):
            v=decimal_value(n,getattr(self,n));
            if v<0:raise ValueError(f"{n} must be nonnegative")
            object.__setattr__(self,n,v)
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
        if not isinstance(self.mode,ExecutionMode) or not isinstance(self.status,PaperBrokerExecutionStatus) or not isinstance(self.rejection_reason,PaperBrokerRejectionReason):raise ValueError("invalid result enum")
        if self.status in (PaperBrokerExecutionStatus.FILLED,PaperBrokerExecutionStatus.ACKNOWLEDGED) and self.rejection_reason is not PaperBrokerRejectionReason.NONE:raise ValueError("successful status requires NONE reason")
        if self.status in (PaperBrokerExecutionStatus.REJECTED,PaperBrokerExecutionStatus.DUPLICATE) and self.rejection_reason is PaperBrokerRejectionReason.NONE:raise ValueError("rejection status requires reason")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"execution_id":self.execution_id,"authorization_id":self.authorization_id,"proposal_id":self.proposal_id,"request_fingerprint":self.request_fingerprint,"symbol":self.symbol,"direction":self.direction.value if self.direction else None,"quantity_requested":self.quantity_requested,"quantity_filled":self.quantity_filled,"entry_price":str(self.entry_price),"fill_price":str(self.fill_price),"filled_notional":str(self.filled_notional),"mode":self.mode.value,"timestamp":self.timestamp.isoformat(),"status":self.status.value,"rejection_reason":self.rejection_reason.value,"policy_version":self.policy_version,"adapter_version":self.adapter_version,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:
            d=dict(v);d["direction"]=TradeDirection(d["direction"]) if d["direction"] else None;d["mode"]=ExecutionMode(d["mode"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);d["status"]=PaperBrokerExecutionStatus(d["status"]);d["rejection_reason"]=PaperBrokerRejectionReason(d["rejection_reason"]);return cls(**d)
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize paper broker result") from e
