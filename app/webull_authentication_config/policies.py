from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class WebullAuthenticationConfigurationLoaderPolicy:
 version:str="webull_authentication_configuration_loader_policy_v1";strict_unknown_fields:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be non-empty")
  if not isinstance(self.strict_unknown_fields,bool):raise ValueError("strict_unknown_fields must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"strict_unknown_fields":self.strict_unknown_fields,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
