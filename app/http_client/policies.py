from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class HTTPClientPolicy:
 client_enabled:bool=False;retries_enabled:bool=False;redirects_enabled:bool=False;cookies_enabled:bool=False;compression_enabled:bool=False;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("client_enabled","retries_enabled","redirects_enabled","cookies_enabled","compression_enabled"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"client_enabled":self.client_enabled,"retries_enabled":self.retries_enabled,"redirects_enabled":self.redirects_enabled,"cookies_enabled":self.cookies_enabled,"compression_enabled":self.compression_enabled,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
