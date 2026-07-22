from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class WebullGatewayPolicy:
 version:str="webull_gateway_policy_v1";gateway_enabled:bool=False;authentication_required:bool=True;account_access_required:bool=True;order_submission_required:bool=True;order_status_required:bool=True;required_environment:str="production-live";metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip() or not isinstance(self.required_environment,str) or not self.required_environment.strip():raise ValueError("version and environment required")
  for n in ("gateway_enabled","authentication_required","account_access_required","order_submission_required","order_status_required"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"gateway_enabled":self.gateway_enabled,"authentication_required":self.authentication_required,"account_access_required":self.account_access_required,"order_submission_required":self.order_submission_required,"order_status_required":self.order_status_required,"required_environment":self.required_environment,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
