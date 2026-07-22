from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidencePolicy:
 version:str="webull_protocol_evidence_policy_v1";enabled:bool=False;minimum_supporting_records:int=2;minimum_independent_groups:int=2;reject_any_contradiction:bool=True;require_reproducible_support:bool=True;allow_synthetic_support:bool=False;strict_validation:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be non-empty")
  for n in ("enabled","reject_any_contradiction","require_reproducible_support","allow_synthetic_support","strict_validation"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  for n in ("minimum_supporting_records","minimum_independent_groups"):
   v=getattr(self,n)
   if isinstance(v,bool) or not isinstance(v,int) or v<0:raise ValueError(f"{n} must be a non-negative integer")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"enabled":self.enabled,"minimum_supporting_records":self.minimum_supporting_records,"minimum_independent_groups":self.minimum_independent_groups,"reject_any_contradiction":self.reject_any_contradiction,"require_reproducible_support":self.require_reproducible_support,"allow_synthetic_support":self.allow_synthetic_support,"strict_validation":self.strict_validation,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
