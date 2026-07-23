from dataclasses import dataclass,field
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class TransportRuntimePolicy:
 version:str="transport_runtime_policy_v1";runtime_enabled:bool=False;telemetry_enabled:bool=False;timeout_required:bool=True;timeout_seconds:Decimal=Decimal("30");retries_enabled:bool=False;rate_limit_enabled:bool=False;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
  for n in ("runtime_enabled","telemetry_enabled","timeout_required","retries_enabled","rate_limit_enabled"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  try:t=Decimal(self.timeout_seconds)
  except (ValueError,TypeError) as e:raise ValueError("timeout_seconds must be Decimal-compatible") from e
  if not t.is_finite() or t<0 or (self.timeout_required and t<=0):raise ValueError("timeout_seconds invalid")
  object.__setattr__(self,"timeout_seconds",t);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"runtime_enabled":self.runtime_enabled,"telemetry_enabled":self.telemetry_enabled,"timeout_required":self.timeout_required,"timeout_seconds":str(self.timeout_seconds),"retries_enabled":self.retries_enabled,"rate_limit_enabled":self.rate_limit_enabled,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
