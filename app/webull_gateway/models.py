from __future__ import annotations
import hashlib,json
from dataclasses import dataclass,field,fields
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping,ClassVar
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.models import aware_timestamp
from app.trade_proposals.policies import decimal_value
from app.webull_gateway.models_base import *
from app.webull_transport import WebullOrderCommand
def _canon(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False)
def _id(kind,data):return hashlib.sha256(_canon({"kind":kind,**data}).encode()).hexdigest()
def _base(timestamp,environment,metadata):
 if not isinstance(environment,str) or not environment.strip():raise ValueError("environment required")
 return aware_timestamp(timestamp),freeze_json_mapping("metadata",metadata)
@dataclass(frozen=True,slots=True)
class LoginRequest:
 timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);request_id:str=field(init=False)
 def __post_init__(self):
  t,m=_base(self.timestamp,self.environment,self.metadata);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"request_id",_id("login",{"timestamp":t.isoformat(),"environment":self.environment,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"timestamp":self.timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"request_id":self.request_id}
 @classmethod
 def from_dict(cls,v):
  x=cls(datetime.fromisoformat(v["timestamp"]),v["environment"],v.get("metadata",{}));
  if x.request_id!=v["request_id"]:raise ValueError("request ID mismatch")
  return x
@dataclass(frozen=True,slots=True)
class LoginResponse:
 authenticated:bool;authentication_timestamp:datetime;expiration_timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.authenticated,bool):raise ValueError("authenticated must be boolean")
  a,m=_base(self.authentication_timestamp,self.environment,self.metadata);e=aware_timestamp(self.expiration_timestamp)
  if e<a:raise ValueError("expiration cannot precede authentication")
  object.__setattr__(self,"authentication_timestamp",a);object.__setattr__(self,"expiration_timestamp",e);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("login_response",{"authenticated":self.authenticated,"authentication_timestamp":a.isoformat(),"expiration_timestamp":e.isoformat(),"environment":self.environment,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"authenticated":self.authenticated,"authentication_timestamp":self.authentication_timestamp.isoformat(),"expiration_timestamp":self.expiration_timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["authenticated"],datetime.fromisoformat(v["authentication_timestamp"]),datetime.fromisoformat(v["expiration_timestamp"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class LogoutRequest(LoginRequest):pass
@dataclass(frozen=True,slots=True)
class LogoutResponse:
 logged_out:bool;timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.logged_out,bool):raise ValueError("logged_out must be boolean")
  t,m=_base(self.timestamp,self.environment,self.metadata);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("logout_response",{"logged_out":self.logged_out,"timestamp":t.isoformat(),"environment":self.environment,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"logged_out":self.logged_out,"timestamp":self.timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["logged_out"],datetime.fromisoformat(v["timestamp"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class AccountRequest(LoginRequest):pass
@dataclass(frozen=True,slots=True)
class AccountResponse:
 buying_power:Decimal;cash_balance:Decimal;portfolio_value:Decimal;positions:Mapping[str,Decimal];day_pnl:Decimal;timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  vals={n:decimal_value(n,getattr(self,n)) for n in ("buying_power","cash_balance","portfolio_value","day_pnl")}
  if any(vals[n]<0 for n in ("buying_power","cash_balance","portfolio_value")):raise ValueError("account values invalid")
  p={}
  for k,v in self.positions.items():
   s=k.strip().upper() if isinstance(k,str) else ""
   if not s or s in p:raise ValueError("positions invalid")
   p[s]=decimal_value("position",v)
  t,m=_base(self.timestamp,self.environment,self.metadata)
  for n,v in vals.items():object.__setattr__(self,n,v)
  object.__setattr__(self,"positions",MappingProxyType(p));object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("account_response",{"buying_power":str(vals["buying_power"]),"cash_balance":str(vals["cash_balance"]),"portfolio_value":str(vals["portfolio_value"]),"positions":{k:str(v) for k,v in p.items()},"day_pnl":str(vals["day_pnl"]),"timestamp":t.isoformat(),"environment":self.environment}))
 def to_dict(self):return {"buying_power":str(self.buying_power),"cash_balance":str(self.cash_balance),"portfolio_value":str(self.portfolio_value),"positions":{k:str(v) for k,v in self.positions.items()},"day_pnl":str(self.day_pnl),"timestamp":self.timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["buying_power"],v["cash_balance"],v["portfolio_value"],v["positions"],v["day_pnl"],datetime.fromisoformat(v["timestamp"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class SubmitOrderRequest:
 command:WebullOrderCommand;timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);request_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.command,WebullOrderCommand):raise ValueError("command required")
  t,m=_base(self.timestamp,self.environment,self.metadata)
  if t<self.command.submitted_at:raise ValueError("timestamp precedes command")
  object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"request_id",_id("submit",{"command":self.command.to_dict(),"timestamp":t.isoformat(),"environment":self.environment,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"command":self.command.to_dict(),"timestamp":self.timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"request_id":self.request_id}
 @classmethod
 def from_dict(cls,v):return cls(WebullOrderCommand.from_dict(v["command"]),datetime.fromisoformat(v["timestamp"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class SubmitOrderResponse:
 accepted:bool;broker_reference:str;quantity:int;price:Decimal;timestamp:datetime;retryable:bool;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.accepted,bool) or not isinstance(self.retryable,bool) or not isinstance(self.broker_reference,str):raise ValueError("response fields invalid")
  if isinstance(self.quantity,bool) or not isinstance(self.quantity,int) or self.quantity<0:raise ValueError("quantity invalid")
  p=decimal_value("price",self.price)
  if p<0 or (self.accepted and not self.broker_reference):raise ValueError("accepted response invalid")
  t=aware_timestamp(self.timestamp);m=freeze_json_mapping("metadata",self.metadata);object.__setattr__(self,"price",p);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("submit_response",{"accepted":self.accepted,"broker_reference":self.broker_reference,"quantity":self.quantity,"price":str(p),"timestamp":t.isoformat(),"retryable":self.retryable,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"accepted":self.accepted,"broker_reference":self.broker_reference,"quantity":self.quantity,"price":str(self.price),"timestamp":self.timestamp.isoformat(),"retryable":self.retryable,"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["accepted"],v["broker_reference"],v["quantity"],v["price"],datetime.fromisoformat(v["timestamp"]),v["retryable"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class CancelOrderRequest:
 broker_reference:str;timestamp:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);request_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.broker_reference,str) or not self.broker_reference.strip():raise ValueError("broker reference required")
  t,m=_base(self.timestamp,self.environment,self.metadata);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"request_id",_id("cancel",{"broker_reference":self.broker_reference,"timestamp":t.isoformat(),"environment":self.environment,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"broker_reference":self.broker_reference,"timestamp":self.timestamp.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata),"request_id":self.request_id}
 @classmethod
 def from_dict(cls,v):return cls(v["broker_reference"],datetime.fromisoformat(v["timestamp"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class CancelOrderResponse:
 cancelled:bool;broker_reference:str;timestamp:datetime;retryable:bool;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.cancelled,bool) or not isinstance(self.retryable,bool) or not isinstance(self.broker_reference,str) or not self.broker_reference:raise ValueError("cancel response invalid")
  t=aware_timestamp(self.timestamp);m=freeze_json_mapping("metadata",self.metadata);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("cancel_response",{"cancelled":self.cancelled,"broker_reference":self.broker_reference,"timestamp":t.isoformat(),"retryable":self.retryable,"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"cancelled":self.cancelled,"broker_reference":self.broker_reference,"timestamp":self.timestamp.isoformat(),"retryable":self.retryable,"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["cancelled"],v["broker_reference"],datetime.fromisoformat(v["timestamp"]),v["retryable"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class OrderStatusRequest(CancelOrderRequest):pass
@dataclass(frozen=True,slots=True)
class OrderStatusResponse:
 broker_reference:str;status:NormalizedOrderStatus;quantity:int;filled_quantity:int;average_price:Decimal;timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict);response_id:str=field(init=False)
 def __post_init__(self):
  if not isinstance(self.broker_reference,str) or not self.broker_reference or not isinstance(self.status,NormalizedOrderStatus):raise ValueError("status response invalid")
  if any(isinstance(x,bool) or not isinstance(x,int) or x<0 for x in (self.quantity,self.filled_quantity)) or self.filled_quantity>self.quantity:raise ValueError("quantities invalid")
  p=decimal_value("average_price",self.average_price)
  if p<0:raise ValueError("price invalid")
  t=aware_timestamp(self.timestamp);m=freeze_json_mapping("metadata",self.metadata);object.__setattr__(self,"average_price",p);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"metadata",m);object.__setattr__(self,"response_id",_id("status_response",{"broker_reference":self.broker_reference,"status":self.status.value,"quantity":self.quantity,"filled_quantity":self.filled_quantity,"average_price":str(p),"timestamp":t.isoformat(),"metadata":thaw_json_value(m)}))
 def to_dict(self):return {"broker_reference":self.broker_reference,"status":self.status.value,"quantity":self.quantity,"filled_quantity":self.filled_quantity,"average_price":str(self.average_price),"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata),"response_id":self.response_id}
 @classmethod
 def from_dict(cls,v):return cls(v["broker_reference"],NormalizedOrderStatus(v["status"]),v["quantity"],v["filled_quantity"],v["average_price"],datetime.fromisoformat(v["timestamp"]),v.get("metadata",{}))
