from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from app.broker_adapter.models_base import *
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.live_broker import LiveBrokerInvocation
from app.trade_proposals.models import aware_timestamp
from app.trade_proposals.policies import decimal_value
@dataclass(frozen=True,slots=True)
class BrokerOrderRequest:
 client_order_id:str;invocation_id:str;authorization_id:str;proposal_id:str;request_fingerprint:str;symbol:str;side:BrokerOrderSide;quantity:int;order_type:BrokerOrderType;limit_price:Decimal;time_in_force:BrokerTimeInForce;submitted_at:datetime;environment:str;policy_version:str;adapter_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("client_order_id","invocation_id","authorization_id","proposal_id","request_fingerprint","symbol","environment","policy_version","adapter_version"):
   if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
  if self.symbol!=self.symbol.upper():raise ValueError("symbol must be uppercase")
  if isinstance(self.quantity,bool) or not isinstance(self.quantity,int) or self.quantity<=0:raise ValueError("quantity must be positive integer")
  price=decimal_value("limit_price",self.limit_price);object.__setattr__(self,"limit_price",price)
  if self.order_type is BrokerOrderType.LIMIT and price<=0:raise ValueError("LIMIT requires positive price")
  if self.order_type is BrokerOrderType.MARKET and price!=0:raise ValueError("MARKET price must be zero")
  object.__setattr__(self,"submitted_at",aware_timestamp(self.submitted_at));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {**{n:getattr(self,n) for n in ("client_order_id","invocation_id","authorization_id","proposal_id","request_fingerprint","symbol","quantity","environment","policy_version","adapter_version")},"side":self.side.value,"order_type":self.order_type.value,"limit_price":str(self.limit_price),"time_in_force":self.time_in_force.value,"submitted_at":self.submitted_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["side"]=BrokerOrderSide(d["side"]);d["order_type"]=BrokerOrderType(d["order_type"]);d["time_in_force"]=BrokerTimeInForce(d["time_in_force"]);d["submitted_at"]=datetime.fromisoformat(d["submitted_at"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class BrokerTransportResponse:
 client_order_id:str;transport_request_id:str;broker_order_reference:str;status:BrokerTransportStatus;accepted_quantity:int;accepted_price:Decimal;timestamp:datetime;rejection_code:str;rejection_message:str;retryable:bool;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.client_order_id,str) or not self.client_order_id.strip() or not isinstance(self.transport_request_id,str) or not self.transport_request_id.strip():raise ValueError("response identifiers must be nonempty")
  if not isinstance(self.broker_order_reference,str) or not isinstance(self.rejection_code,str) or not isinstance(self.rejection_message,str):raise ValueError("response text fields invalid")
  if not isinstance(self.status,BrokerTransportStatus):raise ValueError("status invalid")
  if isinstance(self.accepted_quantity,bool) or not isinstance(self.accepted_quantity,int) or self.accepted_quantity<0:raise ValueError("accepted quantity invalid")
  p=decimal_value("accepted_price",self.accepted_price)
  if p<0:raise ValueError("accepted price invalid")
  object.__setattr__(self,"accepted_price",p);object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if not isinstance(self.retryable,bool):raise ValueError("retryable must be boolean")
  if self.status is BrokerTransportStatus.ACCEPTED and not self.broker_order_reference:raise ValueError("accepted response requires broker reference")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"client_order_id":self.client_order_id,"transport_request_id":self.transport_request_id,"broker_order_reference":self.broker_order_reference,"status":self.status.value,"accepted_quantity":self.accepted_quantity,"accepted_price":str(self.accepted_price),"timestamp":self.timestamp.isoformat(),"rejection_code":self.rejection_code,"rejection_message":self.rejection_message,"retryable":self.retryable,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["status"]=BrokerTransportStatus(d["status"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class BrokerAdapterState:
 timestamp:datetime;submitted_client_order_ids:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));ids=tuple(self.submitted_client_order_ids)
  if any(not isinstance(x,str) or not x.strip() for x in ids) or len(ids)!=len(set(ids)):raise ValueError("submitted IDs invalid")
  object.__setattr__(self,"submitted_client_order_ids",ids);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"timestamp":self.timestamp.isoformat(),"submitted_client_order_ids":list(self.submitted_client_order_ids),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(datetime.fromisoformat(v["timestamp"]),tuple(v.get("submitted_client_order_ids",())),v.get("metadata",{}))
from app.broker_adapter.policies import BrokerAdapterPolicy
@dataclass(frozen=True,slots=True)
class BrokerAdapterRequest:
 invocation:LiveBrokerInvocation;timestamp:datetime;policy:BrokerAdapterPolicy;state:BrokerAdapterState;order_type:BrokerOrderType=BrokerOrderType.LIMIT;time_in_force:BrokerTimeInForce=BrokerTimeInForce.DAY;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.invocation,LiveBrokerInvocation):raise ValueError("invocation must be LiveBrokerInvocation")
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if self.timestamp<self.invocation.timestamp:raise ValueError("request cannot precede invocation")
  if not isinstance(self.policy,BrokerAdapterPolicy) or not isinstance(self.state,BrokerAdapterState):raise ValueError("policy and state invalid")
  if self.state.timestamp>self.timestamp:raise ValueError("state cannot be newer than request")
  if not isinstance(self.order_type,BrokerOrderType) or not isinstance(self.time_in_force,BrokerTimeInForce):raise ValueError("order enums invalid")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"invocation":self.invocation.to_dict(),"timestamp":self.timestamp.isoformat(),"policy":self.policy.to_dict(),"state":self.state.to_dict(),"order_type":self.order_type.value,"time_in_force":self.time_in_force.value,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(LiveBrokerInvocation.from_dict(v["invocation"]),datetime.fromisoformat(v["timestamp"]),BrokerAdapterPolicy.from_dict(v["policy"]),BrokerAdapterState.from_dict(v["state"]),BrokerOrderType(v["order_type"]),BrokerTimeInForce(v["time_in_force"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class BrokerLiveExecutionResult:
 result_id:str;client_order_id:str;invocation_id:str;authorization_id:str;proposal_id:str;request_fingerprint:str;symbol:str;side:BrokerOrderSide;quantity_requested:int;quantity_accepted:int;order_type:BrokerOrderType;requested_price:Decimal;accepted_price:Decimal;time_in_force:BrokerTimeInForce;environment:str;timestamp:datetime;status:BrokerExecutionStatus;reason:BrokerExecutionReason;transport_request_id:str;broker_order_reference:str;retryable:bool;policy_version:str;adapter_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("result_id","client_order_id","invocation_id","authorization_id","proposal_id","request_fingerprint","symbol","environment","policy_version","adapter_version"):
   if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
  for n in ("quantity_requested","quantity_accepted"):
   if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise ValueError("quantities invalid")
  for n in ("requested_price","accepted_price"):object.__setattr__(self,n,decimal_value(n,getattr(self,n)))
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if not isinstance(self.retryable,bool):raise ValueError("retryable invalid")
  if not all(isinstance(x,t) for x,t in ((self.side,BrokerOrderSide),(self.order_type,BrokerOrderType),(self.time_in_force,BrokerTimeInForce),(self.status,BrokerExecutionStatus),(self.reason,BrokerExecutionReason))):raise ValueError("result enums invalid")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):
  d={n:getattr(self,n) for n in ("result_id","client_order_id","invocation_id","authorization_id","proposal_id","request_fingerprint","symbol","quantity_requested","quantity_accepted","environment","transport_request_id","broker_order_reference","retryable","policy_version","adapter_version")};d.update({"side":self.side.value,"order_type":self.order_type.value,"requested_price":str(self.requested_price),"accepted_price":str(self.accepted_price),"time_in_force":self.time_in_force.value,"timestamp":self.timestamp.isoformat(),"status":self.status.value,"reason":self.reason.value,"metadata":thaw_json_value(self.metadata)});return d
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["side"]=BrokerOrderSide(d["side"]);d["order_type"]=BrokerOrderType(d["order_type"]);d["time_in_force"]=BrokerTimeInForce(d["time_in_force"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);d["status"]=BrokerExecutionStatus(d["status"]);d["reason"]=BrokerExecutionReason(d["reason"]);return cls(**d)
