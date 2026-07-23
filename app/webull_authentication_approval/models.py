from dataclasses import dataclass,field
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.webull_authentication import WebullAuthenticationPolicy,WebullAuthenticationProfile
from app.webull_authentication_config import WebullAuthenticationProfileConfigurationResult
from app.webull_protocol_evidence import WebullProtocolEvidenceAssessment
from app.webull_authentication_approval.exceptions import *
from app.webull_authentication_approval.mapping import ALL_MATERIAL_PROFILE_FIELDS
class ProfileApprovalDecision(StrEnum):DISABLED="DISABLED";APPROVED="APPROVED";INSUFFICIENT_EVIDENCE="INSUFFICIENT_EVIDENCE";CONTRADICTED="CONTRADICTED";MISSING_BINDING="MISSING_BINDING";INVALID_ASSESSMENT="INVALID_ASSESSMENT";REJECTED="REJECTED"
def _s(v,n,error=WebullAuthenticationApprovalValidationError):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise error(f"{n} must be a non-empty stripped string")
 return v
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProtocolClaimBinding:
 binding_id:str;profile_field:str;claim_ids:tuple[str,...];required:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"binding_id",_s(self.binding_id,"binding_id",WebullAuthenticationApprovalBindingError));object.__setattr__(self,"profile_field",_s(self.profile_field,"profile_field",WebullAuthenticationApprovalBindingError))
  if self.profile_field not in ALL_MATERIAL_PROFILE_FIELDS:raise WebullAuthenticationApprovalBindingError("binding references an unknown profile field")
  if not isinstance(self.claim_ids,tuple) or not self.claim_ids:raise WebullAuthenticationApprovalBindingError("claim_ids must be a non-empty immutable tuple")
  ids=tuple(_s(x,"claim_id",WebullAuthenticationApprovalBindingError) for x in self.claim_ids)
  if len(set(ids))!=len(ids):raise WebullAuthenticationApprovalBindingError("claim_ids must be unique")
  object.__setattr__(self,"claim_ids",ids)
  if not isinstance(self.required,bool):raise WebullAuthenticationApprovalBindingError("required must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"binding_id":self.binding_id,"profile_field":self.profile_field,"claim_ids":list(self.claim_ids),"required":self.required,"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class WebullAuthenticationAssessmentProvenance:
 claim_id:str;synthetic_support_used:bool;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"claim_id",_s(self.claim_id,"claim_id"))
  if not isinstance(self.synthetic_support_used,bool):raise WebullAuthenticationApprovalAssessmentError("synthetic_support_used must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"claim_id":self.claim_id,"synthetic_support_used":self.synthetic_support_used,"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfileApprovalRequest:
 approval_id:str;configuration_result:WebullAuthenticationProfileConfigurationResult;evidence_assessments:tuple[WebullProtocolEvidenceAssessment,...];claim_bindings:tuple[WebullAuthenticationProtocolClaimBinding,...];assessment_provenance:tuple[WebullAuthenticationAssessmentProvenance,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"approval_id",_s(self.approval_id,"approval_id"))
  if not isinstance(self.configuration_result,WebullAuthenticationProfileConfigurationResult):raise WebullAuthenticationApprovalValidationError("configuration_result has invalid type")
  if not isinstance(self.evidence_assessments,tuple) or any(not isinstance(x,WebullProtocolEvidenceAssessment) for x in self.evidence_assessments):raise WebullAuthenticationApprovalAssessmentError("evidence_assessments must be an immutable assessment tuple")
  if not isinstance(self.claim_bindings,tuple) or any(not isinstance(x,WebullAuthenticationProtocolClaimBinding) for x in self.claim_bindings):raise WebullAuthenticationApprovalBindingError("claim_bindings must be an immutable binding tuple")
  if not isinstance(self.assessment_provenance,tuple) or any(not isinstance(x,WebullAuthenticationAssessmentProvenance) for x in self.assessment_provenance):raise WebullAuthenticationApprovalAssessmentError("assessment_provenance must be an immutable provenance tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"approval_id":self.approval_id,"configuration_id":self.configuration_result.configuration_id,"profile_id":self.configuration_result.profile.profile_id,"evidence_assessments":[x.to_dict() for x in self.evidence_assessments],"claim_bindings":[x.to_dict() for x in self.claim_bindings],"assessment_provenance":[x.to_dict() for x in self.assessment_provenance],"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfileApprovalResult:
 approval_id:str;decision:ProfileApprovalDecision;approved:bool;configuration_id:str;profile_id:str;approved_profile:WebullAuthenticationProfile|None;approved_policy:WebullAuthenticationPolicy|None;evaluated_binding_ids:tuple[str,...];missing_binding_fields:tuple[str,...];missing_claim_ids:tuple[str,...];rejected_claim_ids:tuple[str,...];contradicted_claim_ids:tuple[str,...];disabled_claim_ids:tuple[str,...];criteria_results:Mapping[str,bool];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if self.approved!=(self.decision is ProfileApprovalDecision.APPROVED):raise WebullAuthenticationApprovalValidationError("approved flag must match decision")
  if self.approved and (not isinstance(self.approved_profile,WebullAuthenticationProfile) or not isinstance(self.approved_policy,WebullAuthenticationPolicy)):raise WebullAuthenticationApprovalValidationError("approved result requires validated artifacts")
  if not self.approved and (self.approved_profile is not None or self.approved_policy is not None):raise WebullAuthenticationApprovalValidationError("rejected result cannot expose approved artifacts")
  object.__setattr__(self,"criteria_results",freeze_json_mapping("criteria_results",self.criteria_results));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"approval_id":self.approval_id,"decision":self.decision.value,"approved":self.approved,"configuration_id":self.configuration_id,"profile_id":self.profile_id,"approved_profile":self.approved_profile.to_dict() if self.approved_profile else None,"approved_policy":self.approved_policy.to_dict() if self.approved_policy else None,"evaluated_binding_ids":list(self.evaluated_binding_ids),"missing_binding_fields":list(self.missing_binding_fields),"missing_claim_ids":list(self.missing_claim_ids),"rejected_claim_ids":list(self.rejected_claim_ids),"contradicted_claim_ids":list(self.contradicted_claim_ids),"disabled_claim_ids":list(self.disabled_claim_ids),"criteria_results":thaw_json_value(self.criteria_results),"metadata":thaw_json_value(self.metadata)}
