from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfileApprovalPolicy:
 version:str="webull_authentication_profile_approval_policy_v1";enabled:bool=False;require_all_material_fields_bound:bool=True;require_all_assessments_eligible:bool=True;reject_contradicted_assessments:bool=True;reject_disabled_assessments:bool=True;reject_missing_assessments:bool=True;allow_synthetic_evidence:bool=False;strict_validation:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be non-empty")
  for n in ("enabled","require_all_material_fields_bound","require_all_assessments_eligible","reject_contradicted_assessments","reject_disabled_assessments","reject_missing_assessments","allow_synthetic_evidence","strict_validation"):
   if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"version":self.version,"enabled":self.enabled,"require_all_material_fields_bound":self.require_all_material_fields_bound,"require_all_assessments_eligible":self.require_all_assessments_eligible,"reject_contradicted_assessments":self.reject_contradicted_assessments,"reject_disabled_assessments":self.reject_disabled_assessments,"reject_missing_assessments":self.reject_missing_assessments,"allow_synthetic_evidence":self.allow_synthetic_evidence,"strict_validation":self.strict_validation,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
