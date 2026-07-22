from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.policies import decimal_value
from app.broker_adapter.models_base import BrokerOrderType,BrokerTimeInForce
@dataclass(frozen=True,slots=True)
class BrokerAdapterPolicy:
 version:str="broker_adapter_policy_v1";adapter_enabled:bool=False;require_ready_invocation:bool=True;require_live_mode:bool=True;allowed_order_types:tuple[BrokerOrderType,...]=(BrokerOrderType.LIMIT,);allowed_time_in_force:tuple[BrokerTimeInForce,...]=(BrokerTimeInForce.DAY,);maximum_quantity:int=0;maximum_notional:Decimal=Decimal("0");reject_duplicate_client_order_ids:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
  for n in ("adapter_enabled","require_ready_invocation","require_live_mode","reject_duplicate_client_order_ids"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  for n,t in (("allowed_order_types",BrokerOrderType),("allowed_time_in_force",BrokerTimeInForce)):
   v=tuple(getattr(self,n)) if isinstance(getattr(self,n),(tuple,list)) else ()
   if not v or any(not isinstance(x,t) for x in v) or len(v)!=len(set(v)):raise ValueError(f"{n} must be nonempty unique enums")
   object.__setattr__(self,n,v)
  if isinstance(self.maximum_quantity,bool) or not isinstance(self.maximum_quantity,int) or self.maximum_quantity<0:raise ValueError("maximum_quantity must be nonnegative integer")
  m=decimal_value("maximum_notional",self.maximum_notional)
  if m<0:raise ValueError("maximum_notional must be nonnegative")
  object.__setattr__(self,"maximum_notional",m);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"adapter_enabled":self.adapter_enabled,"require_ready_invocation":self.require_ready_invocation,"require_live_mode":self.require_live_mode,"allowed_order_types":[x.value for x in self.allowed_order_types],"allowed_time_in_force":[x.value for x in self.allowed_time_in_force],"maximum_quantity":self.maximum_quantity,"maximum_notional":str(self.maximum_notional),"reject_duplicate_client_order_ids":self.reject_duplicate_client_order_ids,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["allowed_order_types"]=tuple(BrokerOrderType(x) for x in d["allowed_order_types"]);d["allowed_time_in_force"]=tuple(BrokerTimeInForce(x) for x in d["allowed_time_in_force"]);return cls(**d)
