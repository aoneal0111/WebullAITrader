from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class WebullAuthenticationPolicy:
 version:str="webull_authentication_policy_v1";enabled:bool=False;strict_validation:bool=True;include_device_identifier:bool=False;require_success_indicator:bool=True;reject_unexpected_success_values:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be non-empty")
  for n in ("enabled","strict_validation","include_device_identifier","require_success_indicator","reject_unexpected_success_values"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"enabled":self.enabled,"strict_validation":self.strict_validation,"include_device_identifier":self.include_device_identifier,"require_success_indicator":self.require_success_indicator,"reject_unexpected_success_values":self.reject_unexpected_success_values,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
