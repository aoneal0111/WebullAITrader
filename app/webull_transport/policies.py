from dataclasses import dataclass,field
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.policies import decimal_value
@dataclass(frozen=True,slots=True)
class WebullTransportPolicy:
 version:str="webull_transport_policy_v1";transport_enabled:bool=False;require_limit_orders:bool=True;require_day_time_in_force:bool=True;maximum_quantity:int=0;maximum_notional:Decimal=Decimal("0");allowed_symbols:tuple[str,...]=();required_environment:str="production-live";reject_duplicate_transport_request_ids:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
  for n in ("transport_enabled","require_limit_orders","require_day_time_in_force","reject_duplicate_transport_request_ids"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  if isinstance(self.maximum_quantity,bool) or not isinstance(self.maximum_quantity,int) or self.maximum_quantity<0:raise ValueError("maximum_quantity invalid")
  m=decimal_value("maximum_notional",self.maximum_notional)
  if m<0:raise ValueError("maximum_notional invalid")
  object.__setattr__(self,"maximum_notional",m)
  symbols=tuple(x.strip().upper() if isinstance(x,str) else "" for x in self.allowed_symbols)
  if any(not x for x in symbols) or len(symbols)!=len(set(symbols)):raise ValueError("allowed_symbols invalid")
  object.__setattr__(self,"allowed_symbols",symbols)
  if not isinstance(self.required_environment,str) or not self.required_environment.strip():raise ValueError("required_environment invalid")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"transport_enabled":self.transport_enabled,"require_limit_orders":self.require_limit_orders,"require_day_time_in_force":self.require_day_time_in_force,"maximum_quantity":self.maximum_quantity,"maximum_notional":str(self.maximum_notional),"allowed_symbols":list(self.allowed_symbols),"required_environment":self.required_environment,"reject_duplicate_transport_request_ids":self.reject_duplicate_transport_request_ids,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
