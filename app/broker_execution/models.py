from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.trade_proposals.models import TradeDirection, TradeProposal, aware_timestamp
from app.trade_proposals.policies import decimal_value
from app.broker_execution.policies import ExecutionSafetyPolicy

class ExecutionMode(StrEnum): PAPER="PAPER"; LIVE="LIVE"
class SafetyDecision(StrEnum): APPROVED="APPROVED"; REJECTED="REJECTED"
class SafetyReason(StrEnum):
    APPROVED="APPROVED"; PROPOSAL_NOT_READY="PROPOSAL_NOT_READY"; KILL_SWITCH_ACTIVE="KILL_SWITCH_ACTIVE"; LIVE_MODE_DISABLED="LIVE_MODE_DISABLED"
    HUMAN_AUTHORIZATION_REQUIRED="HUMAN_AUTHORIZATION_REQUIRED"; HUMAN_AUTHORIZATION_INVALID="HUMAN_AUTHORIZATION_INVALID"
    QUANTITY_EXCEEDS_LIMIT="QUANTITY_EXCEEDS_LIMIT"; NOTIONAL_EXCEEDS_LIMIT="NOTIONAL_EXCEEDS_LIMIT"; POSITION_EXCEEDS_LIMIT="POSITION_EXCEEDS_LIMIT"
    DAILY_LOSS_LIMIT_REACHED="DAILY_LOSS_LIMIT_REACHED"; DUPLICATE_REQUEST="DUPLICATE_REQUEST"; INVALID_QUANTITY="INVALID_QUANTITY"
    INVALID_ENTRY_PRICE="INVALID_ENTRY_PRICE"; INVALID_SYMBOL="INVALID_SYMBOL"; INVALID_TIMESTAMP="INVALID_TIMESTAMP"

@dataclass(frozen=True,slots=True)
class SafetyCheck:
    name:str; passed:bool; detail:str
    def __post_init__(self):
        if self.name not in CHECK_NAMES: raise ValueError("unrecognized safety check")
        if not isinstance(self.passed,bool): raise ValueError("passed must be boolean")
        if not isinstance(self.detail,str) or not self.detail: raise ValueError("detail must be nonempty")
    def to_dict(self): return {"name":self.name,"passed":self.passed,"detail":self.detail}
    @classmethod
    def from_dict(cls,v):
        try:return cls(v["name"],v["passed"],v["detail"])
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize safety check") from e

CHECK_NAMES=("request valid","proposal ready","symbol valid","quantity positive","entry price positive","kill switch inactive","mode permitted","human authorization valid","duplicate request absent","quantity within limit","order notional within limit","projected position within limit","daily loss limit not reached")

@dataclass(frozen=True,slots=True)
class BrokerAccountSnapshot:
    timestamp:datetime; current_daily_realized_pnl:Decimal
    symbol_positions:Mapping[str,Decimal]=field(default_factory=dict)
    recent_authorization_fingerprints:tuple[str,...]=()
    metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp)); object.__setattr__(self,"current_daily_realized_pnl",decimal_value("current_daily_realized_pnl",self.current_daily_realized_pnl))
        if not isinstance(self.symbol_positions,Mapping): raise ValueError("symbol_positions must be a mapping")
        p={}
        for k,v in self.symbol_positions.items():
            s=k.strip().upper() if isinstance(k,str) else ""
            if not s or s in p: raise ValueError("position symbols must be unique and nonempty")
            p[s]=decimal_value("symbol position",v)
        object.__setattr__(self,"symbol_positions",MappingProxyType(p))
        if not isinstance(self.recent_authorization_fingerprints,(tuple,list)) or any(not isinstance(x,str) or not x.strip() for x in self.recent_authorization_fingerprints): raise ValueError("fingerprints must be nonempty strings")
        object.__setattr__(self,"recent_authorization_fingerprints",tuple(x.strip() for x in self.recent_authorization_fingerprints)); object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self): return {"timestamp":self.timestamp.isoformat(),"current_daily_realized_pnl":str(self.current_daily_realized_pnl),"symbol_positions":{k:str(v) for k,v in self.symbol_positions.items()},"recent_authorization_fingerprints":list(self.recent_authorization_fingerprints),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(datetime.fromisoformat(v["timestamp"]),v["current_daily_realized_pnl"],v.get("symbol_positions",{}),tuple(v.get("recent_authorization_fingerprints",())),v.get("metadata",{}))
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize account snapshot") from e

@dataclass(frozen=True,slots=True)
class HumanAuthorization:
    authorization_id:str; proposal_id:str; approved:bool; timestamp:datetime; expires_at:datetime; authorized_mode:ExecutionMode; metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("authorization_id","proposal_id"):
            v=getattr(self,n)
            if not isinstance(v,str) or not v.strip():raise ValueError(f"{n} must be nonempty")
            object.__setattr__(self,n,v.strip())
        if not isinstance(self.approved,bool):raise ValueError("approved must be boolean")
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"expires_at",aware_timestamp(self.expires_at))
        if self.expires_at<self.timestamp:raise ValueError("expires_at cannot precede timestamp")
        if not isinstance(self.authorized_mode,ExecutionMode):raise ValueError("authorized_mode must be ExecutionMode")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"authorization_id":self.authorization_id,"proposal_id":self.proposal_id,"approved":self.approved,"timestamp":self.timestamp.isoformat(),"expires_at":self.expires_at.isoformat(),"authorized_mode":self.authorized_mode.value,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(v["authorization_id"],v["proposal_id"],v["approved"],datetime.fromisoformat(v["timestamp"]),datetime.fromisoformat(v["expires_at"]),ExecutionMode(v["authorized_mode"]),v.get("metadata",{}))
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize human authorization") from e

@dataclass(frozen=True,slots=True)
class BrokerExecutionRequest:
    proposal:TradeProposal; mode:ExecutionMode; timestamp:datetime; policy:ExecutionSafetyPolicy; account_snapshot:BrokerAccountSnapshot; human_authorization:HumanAuthorization|None; request_fingerprint:str; metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.proposal,TradeProposal):raise ValueError("proposal must be a TradeProposal")
        if not isinstance(self.mode,ExecutionMode):raise ValueError("mode must be ExecutionMode")
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
        if self.timestamp<self.proposal.timestamp:raise ValueError("request timestamp cannot precede proposal timestamp")
        if not isinstance(self.policy,ExecutionSafetyPolicy):raise ValueError("policy must be ExecutionSafetyPolicy")
        if not isinstance(self.account_snapshot,BrokerAccountSnapshot):raise ValueError("account_snapshot is required")
        if self.account_snapshot.timestamp>self.timestamp:raise ValueError("account snapshot cannot be newer than request")
        if self.human_authorization is not None and not isinstance(self.human_authorization,HumanAuthorization):raise ValueError("human_authorization must be HumanAuthorization or None")
        if not isinstance(self.request_fingerprint,str) or not self.request_fingerprint.strip():raise ValueError("request_fingerprint must be nonempty")
        object.__setattr__(self,"request_fingerprint",self.request_fingerprint.strip());object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"proposal":self.proposal.to_dict(),"mode":self.mode.value,"timestamp":self.timestamp.isoformat(),"policy":self.policy.to_dict(),"account_snapshot":self.account_snapshot.to_dict(),"human_authorization":self.human_authorization.to_dict() if self.human_authorization else None,"request_fingerprint":self.request_fingerprint,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(TradeProposal.from_dict(v["proposal"]),ExecutionMode(v["mode"]),datetime.fromisoformat(v["timestamp"]),ExecutionSafetyPolicy.from_dict(v["policy"]),BrokerAccountSnapshot.from_dict(v["account_snapshot"]),HumanAuthorization.from_dict(v["human_authorization"]) if v.get("human_authorization") else None,v["request_fingerprint"],v.get("metadata",{}))
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize broker execution request") from e

@dataclass(frozen=True,slots=True)
class BrokerExecutionAuthorization:
    authorization_id:str; request_fingerprint:str; proposal_id:str; symbol:str; direction:TradeDirection|None; quantity:Decimal; entry_price:Decimal; order_notional:Decimal; projected_symbol_position:Decimal; mode:ExecutionMode; timestamp:datetime; decision:SafetyDecision; reason:SafetyReason; policy_version:str; safety_engine_version:str; human_authorization_id:str|None; checks:tuple[SafetyCheck,...]; metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("authorization_id","request_fingerprint","proposal_id","symbol","policy_version","safety_engine_version"):
            v=getattr(self,n)
            if not isinstance(v,str) or not v.strip():raise ValueError(f"{n} must be nonempty")
        for n in ("quantity","entry_price","order_notional","projected_symbol_position"):object.__setattr__(self,n,decimal_value(n,getattr(self,n)))
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
        if not isinstance(self.mode,ExecutionMode) or not isinstance(self.decision,SafetyDecision) or not isinstance(self.reason,SafetyReason):raise ValueError("invalid authorization enum")
        if self.decision is SafetyDecision.APPROVED and self.reason is not SafetyReason.APPROVED:raise ValueError("approved decision requires APPROVED reason")
        if self.decision is SafetyDecision.REJECTED and self.reason is SafetyReason.APPROVED:raise ValueError("rejected decision requires rejection reason")
        if not isinstance(self.checks,tuple) or tuple(x.name for x in self.checks)!=CHECK_NAMES:raise ValueError("checks must use stable order")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):
        return {"authorization_id":self.authorization_id,"request_fingerprint":self.request_fingerprint,"proposal_id":self.proposal_id,"symbol":self.symbol,"direction":self.direction.value if self.direction else None,"quantity":str(self.quantity),"entry_price":str(self.entry_price),"order_notional":str(self.order_notional),"projected_symbol_position":str(self.projected_symbol_position),"mode":self.mode.value,"timestamp":self.timestamp.isoformat(),"decision":self.decision.value,"reason":self.reason.value,"policy_version":self.policy_version,"safety_engine_version":self.safety_engine_version,"human_authorization_id":self.human_authorization_id,"checks":[x.to_dict() for x in self.checks],"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:
            d=dict(v);d["direction"]=TradeDirection(d["direction"]) if d["direction"] else None;d["mode"]=ExecutionMode(d["mode"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);d["decision"]=SafetyDecision(d["decision"]);d["reason"]=SafetyReason(d["reason"]);d["checks"]=tuple(SafetyCheck.from_dict(x) for x in d["checks"]);return cls(**d)
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize broker execution authorization") from e
