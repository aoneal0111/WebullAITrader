from app.webull_authentication_approval.exceptions import WebullAuthenticationApprovalDependencyError
from app.webull_authentication_approval.mapping import required_material_fields
from app.webull_authentication_approval.models import *
from app.webull_authentication_approval.policies import WebullAuthenticationProfileApprovalPolicy
from app.webull_authentication_approval.validation import validate_request
from app.webull_protocol_evidence import EvidenceDecision
class DeterministicWebullAuthenticationProfileApprovalService:
 def __init__(self,policy):
  if not isinstance(policy,WebullAuthenticationProfileApprovalPolicy):raise WebullAuthenticationApprovalDependencyError("policy must be WebullAuthenticationProfileApprovalPolicy")
  self._policy=policy
 def approve(self,request):
  r=validate_request(request,self._policy.strict_validation);required=required_material_fields(r.configuration_result);bindings={x.profile_field:x for x in r.claim_bindings};missing_fields=tuple(x for x in required if x not in bindings or not bindings[x].required);referenced=tuple(claim for binding in r.claim_bindings for claim in binding.claim_ids);assessments={x.claim_id:x for x in r.evidence_assessments};provenance={x.claim_id:x for x in r.assessment_provenance};missing_claims=tuple(x for x in referenced if x not in assessments);contradicted=tuple(x for x in referenced if x in assessments and assessments[x].decision is EvidenceDecision.CONTRADICTED);disabled=tuple(x for x in referenced if x in assessments and assessments[x].decision is EvidenceDecision.DISABLED);ineligible=tuple(x for x in referenced if x in assessments and not assessments[x].eligible_for_profile_use);synthetic=tuple(x for x in referenced if x in provenance and provenance[x].synthetic_support_used)
  criteria={"service_enabled":self._policy.enabled,"material_fields_bound":not missing_fields,"assessments_present":not missing_claims,"contradiction_free":not contradicted,"disabled_free":not disabled,"assessments_eligible":not ineligible,"synthetic_evidence_allowed":self._policy.allow_synthetic_evidence or not synthetic}
  if not self._policy.enabled:decision=ProfileApprovalDecision.DISABLED
  elif self._policy.require_all_material_fields_bound and missing_fields:decision=ProfileApprovalDecision.MISSING_BINDING
  elif self._policy.reject_missing_assessments and missing_claims:decision=ProfileApprovalDecision.INSUFFICIENT_EVIDENCE
  elif self._policy.reject_contradicted_assessments and contradicted:decision=ProfileApprovalDecision.CONTRADICTED
  elif self._policy.reject_disabled_assessments and disabled:decision=ProfileApprovalDecision.INVALID_ASSESSMENT
  elif self._policy.require_all_assessments_eligible and ineligible:decision=ProfileApprovalDecision.INSUFFICIENT_EVIDENCE
  elif not self._policy.allow_synthetic_evidence and synthetic:decision=ProfileApprovalDecision.REJECTED
  else:decision=ProfileApprovalDecision.APPROVED
  approved=decision is ProfileApprovalDecision.APPROVED;c=r.configuration_result
  return WebullAuthenticationProfileApprovalResult(r.approval_id,decision,approved,c.configuration_id,c.profile.profile_id,c.profile if approved else None,c.policy if approved else None,tuple(x.binding_id for x in r.claim_bindings),missing_fields,missing_claims,tuple(dict.fromkeys(ineligible+synthetic)),contradicted,disabled,criteria,{"deterministic":True,"approval_scope":"eligibility-only"})
