from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.strategy.exceptions import StrategyValidationError
@dataclass(frozen=True,slots=True)
class StrategyPolicy:
 version:str="strategy_policy_v1";enabled:bool=False;strict_validation:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise StrategyValidationError("version must be a non-empty stripped string")
  if not isinstance(self.enabled,bool) or not isinstance(self.strict_validation,bool):raise StrategyValidationError("policy flags must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"enabled":self.enabled,"strict_validation":self.strict_validation,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except StrategyValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise StrategyValidationError("invalid strategy policy") from exc
