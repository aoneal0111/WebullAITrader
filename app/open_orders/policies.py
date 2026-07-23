from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.open_orders.exceptions import OpenOrdersValidationError
@dataclass(frozen=True,slots=True)
class OpenOrdersPolicy:
 version:str="open_orders_policy_v1";enabled:bool=False;strict_validation:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise OpenOrdersValidationError("version must be a non-empty stripped string")
  if not isinstance(self.enabled,bool):raise OpenOrdersValidationError("enabled must be boolean")
  if not isinstance(self.strict_validation,bool):raise OpenOrdersValidationError("strict_validation must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"enabled":self.enabled,"strict_validation":self.strict_validation,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except OpenOrdersValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OpenOrdersValidationError("invalid open orders policy") from exc
