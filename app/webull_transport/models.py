from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from app.broker_adapter import BrokerOrderRequest
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.models import aware_timestamp
from app.trade_proposals.policies import decimal_value
from app.webull_transport.models_base import *
@dataclass(frozen=True,slots=True)
class WebullOrderCommand:
 transport_request_id:str;client_order_id:str;symbol:str;action:WebullOrderAction;quantity:int;order_type:WebullOrderType;limit_price:Decimal;time_in_force:WebullTimeInForce;environment:str;submitted_at:datetime;adapter_version:str;transport_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("transport_request_id","client_order_id","symbol","environment","adapter_version","transport_version"):
   if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
  if self.symbol!=self.symbol.upper() or isinstance(self.quantity,bool) or not isinstance(self.quantity,int) or self.quantity<=0:raise ValueError("symbol or quantity invalid")
  p=decimal_value("limit_price",self.limit_price);object.__setattr__(self,"limit_price",p)
  if (self.order_type is WebullOrderType.LMT and p<=0) or (self.order_type is WebullOrderType.MKT and p!=0):raise ValueError("price invalid")
  object.__setattr__(self,"submitted_at",aware_timestamp(self.submitted_at));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"transport_request_id":self.transport_request_id,"client_order_id":self.client_order_id,"symbol":self.symbol,"action":self.action.value,"quantity":self.quantity,"order_type":self.order_type.value,"limit_price":str(self.limit_price),"time_in_force":self.time_in_force.value,"environment":self.environment,"submitted_at":self.submitted_at.isoformat(),"adapter_version":self.adapter_version,"transport_version":self.transport_version,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["action"]=WebullOrderAction(d["action"]);d["order_type"]=WebullOrderType(d["order_type"]);d["time_in_force"]=WebullTimeInForce(d["time_in_force"]);d["submitted_at"]=datetime.fromisoformat(d["submitted_at"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class WebullGatewayResponse:
 transport_request_id:str;client_order_id:str;broker_order_reference:str;status:WebullGatewayStatus;accepted_quantity:int;accepted_price:Decimal;timestamp:datetime;rejection_code:str="";rejection_message:str="";retryable:bool=False;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not self.transport_request_id or not self.client_order_id:raise ValueError("identifiers required")
  if isinstance(self.accepted_quantity,bool) or not isinstance(self.accepted_quantity,int) or self.accepted_quantity<0:raise ValueError("quantity invalid")
  p=decimal_value("accepted_price",self.accepted_price)
  if p<0:raise ValueError("price invalid")
  object.__setattr__(self,"accepted_price",p);object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if not isinstance(self.retryable,bool):raise ValueError("retryable invalid")
  if self.status is WebullGatewayStatus.ACCEPTED and not self.broker_order_reference:raise ValueError("accepted requires reference")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"transport_request_id":self.transport_request_id,"client_order_id":self.client_order_id,"broker_order_reference":self.broker_order_reference,"status":self.status.value,"accepted_quantity":self.accepted_quantity,"accepted_price":str(self.accepted_price),"timestamp":self.timestamp.isoformat(),"rejection_code":self.rejection_code,"rejection_message":self.rejection_message,"retryable":self.retryable,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["status"]=WebullGatewayStatus(d["status"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class WebullTransportState:
 timestamp:datetime;submitted_transport_request_ids:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));ids=tuple(self.submitted_transport_request_ids)
  if any(not isinstance(x,str) or not x.strip() for x in ids) or len(ids)!=len(set(ids)):raise ValueError("IDs invalid")
  object.__setattr__(self,"submitted_transport_request_ids",ids);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"timestamp":self.timestamp.isoformat(),"submitted_transport_request_ids":list(self.submitted_transport_request_ids),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(datetime.fromisoformat(v["timestamp"]),tuple(v.get("submitted_transport_request_ids",())),v.get("metadata",{}))
from app.webull_transport.policies import WebullTransportPolicy
@dataclass(frozen=True,slots=True)
class WebullTransportRequest:
 broker_order_request:BrokerOrderRequest;timestamp:datetime;policy:WebullTransportPolicy;state:WebullTransportState;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.broker_order_request,BrokerOrderRequest):raise ValueError("broker_order_request invalid")
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if self.timestamp<self.broker_order_request.submitted_at:raise ValueError("timestamp precedes request")
  if not isinstance(self.policy,WebullTransportPolicy) or not isinstance(self.state,WebullTransportState) or self.state.timestamp>self.timestamp:raise ValueError("policy or state invalid")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"broker_order_request":self.broker_order_request.to_dict(),"timestamp":self.timestamp.isoformat(),"policy":self.policy.to_dict(),"state":self.state.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(BrokerOrderRequest.from_dict(v["broker_order_request"]),datetime.fromisoformat(v["timestamp"]),WebullTransportPolicy.from_dict(v["policy"]),WebullTransportState.from_dict(v["state"]),v.get("metadata",{}))
