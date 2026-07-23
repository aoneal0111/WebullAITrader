from app.webull_authentication_approval.exceptions import *
from app.webull_authentication_approval.models import WebullAuthenticationProfileApprovalRequest
def validate_request(r,strict=True):
 if not isinstance(r,WebullAuthenticationProfileApprovalRequest):raise WebullAuthenticationApprovalValidationError("request must be WebullAuthenticationProfileApprovalRequest")
 binding_ids=[x.binding_id for x in r.claim_bindings];fields=[x.profile_field for x in r.claim_bindings];assessment_ids=[x.claim_id for x in r.evidence_assessments];provenance_ids=[x.claim_id for x in r.assessment_provenance]
 if len(set(binding_ids))!=len(binding_ids):raise WebullAuthenticationApprovalConflictError("duplicate binding identifier")
 if len(set(fields))!=len(fields):raise WebullAuthenticationApprovalConflictError("conflicting profile-field binding")
 if len(set(assessment_ids))!=len(assessment_ids):raise WebullAuthenticationApprovalConflictError("duplicate assessment identity")
 if len(set(provenance_ids))!=len(provenance_ids):raise WebullAuthenticationApprovalConflictError("duplicate provenance identity")
 referenced={claim for binding in r.claim_bindings for claim in binding.claim_ids}
 if strict and (set(assessment_ids)-referenced or set(provenance_ids)-referenced):raise WebullAuthenticationApprovalAssessmentError("unrecognized assessment or provenance")
 return r
