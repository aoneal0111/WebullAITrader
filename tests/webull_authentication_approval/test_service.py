from app.webull_authentication_approval import *
from app.webull_protocol_evidence import EvidenceDecision
from tests.webull_authentication_approval.fixtures import assessment,bindings,policy,request
def approve(r=None,p=None):return DeterministicWebullAuthenticationProfileApprovalService(p or policy()).approve(r or request())
def replace_claim(target):
 r=request();return tuple(target if x.claim_id==target.claim_id else x for x in r.evidence_assessments)
def test_disabled_precedence_and_approved_artifact_identity():
 disabled=approve(p=WebullAuthenticationProfileApprovalPolicy());assert disabled.decision is ProfileApprovalDecision.DISABLED and disabled.approved_profile is None
 r=request();result=approve(r);assert result.approved;assert result.approval_id==r.approval_id;assert result.configuration_id==r.configuration_result.configuration_id;assert result.profile_id==r.configuration_result.profile.profile_id;assert result.approved_profile is r.configuration_result.profile;assert result.approved_policy is r.configuration_result.policy
def test_missing_binding_and_missing_assessment_rejections():
 r=request();missing_binding=approve(request(binding_values=r.claim_bindings[1:],assessments=r.evidence_assessments[1:],provenance=r.assessment_provenance[1:]));assert missing_binding.decision is ProfileApprovalDecision.MISSING_BINDING;assert missing_binding.approved_profile is None
 missing_assessment=approve(request(assessments=r.evidence_assessments[1:],provenance=r.assessment_provenance[1:]));assert missing_assessment.decision is ProfileApprovalDecision.INSUFFICIENT_EVIDENCE;assert missing_assessment.missing_claim_ids
def test_contradicted_disabled_and_ineligible_precedence():
 contradicted=assessment("claim-endpoint_url",EvidenceDecision.CONTRADICTED,False);result=approve(request(assessments=replace_claim(contradicted)));assert result.decision is ProfileApprovalDecision.CONTRADICTED
 disabled=assessment("claim-endpoint_url",EvidenceDecision.DISABLED,False);result=approve(request(assessments=replace_claim(disabled)));assert result.decision is ProfileApprovalDecision.INVALID_ASSESSMENT
 insufficient=assessment("claim-endpoint_url",EvidenceDecision.INSUFFICIENT,False);result=approve(request(assessments=replace_claim(insufficient)));assert result.decision is ProfileApprovalDecision.INSUFFICIENT_EVIDENCE
def test_synthetic_policy_rejection_and_explicit_allowance():
 r=request();p=tuple(WebullAuthenticationAssessmentProvenance(x.claim_id,x.claim_id=="claim-endpoint_url") for x in r.evidence_assessments);blocked=approve(request(provenance=p));allowed=approve(request(provenance=p),policy(allow_synthetic_evidence=True));assert blocked.decision is ProfileApprovalDecision.REJECTED;assert not blocked.approved;assert allowed.approved
def test_equivalent_inputs_deterministic_and_source_immutable():
 r=request();before=r.to_dict();assert approve(r)==approve(request());assert r.to_dict()==before
