import pytest
from app.webull_authentication_approval import *
from tests.webull_authentication_approval.fixtures import request
def test_invalid_dependency_and_request_normalized_without_payload():
 with pytest.raises(WebullAuthenticationApprovalDependencyError):DeterministicWebullAuthenticationProfileApprovalService(object())
 service=DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy(enabled=True))
 with pytest.raises(WebullAuthenticationApprovalValidationError) as captured:service.approve(object())
 assert "metadata" not in str(captured.value)
def test_structural_errors_are_distinct_from_rejection_results():
 r=request();binding=r.claim_bindings[0]
 duplicate=WebullAuthenticationProtocolClaimBinding("duplicate",binding.profile_field,("other-claim",))
 malformed=WebullAuthenticationProfileApprovalRequest(r.approval_id,r.configuration_result,r.evidence_assessments,r.claim_bindings+(duplicate,),r.assessment_provenance)
 with pytest.raises(WebullAuthenticationApprovalConflictError):DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy(enabled=True)).approve(malformed)
