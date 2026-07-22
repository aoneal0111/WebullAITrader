from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication_approval import *
from tests.webull_authentication_approval.fixtures import request
def test_request_binding_provenance_frozen_and_exact_existing_models():
 r=request();assert not hasattr(r,"__dict__");assert type(r.configuration_result).__name__=="WebullAuthenticationProfileConfigurationResult";assert type(r.evidence_assessments[0]).__name__=="WebullProtocolEvidenceAssessment"
 with pytest.raises(FrozenInstanceError):r.approval_id="x"
 with pytest.raises(TypeError):r.metadata["x"]=1
def test_unknown_field_and_duplicate_claims_rejected_in_binding():
 with pytest.raises(WebullAuthenticationApprovalBindingError):WebullAuthenticationProtocolClaimBinding("b","unknown",("c",))
 with pytest.raises(WebullAuthenticationApprovalBindingError):WebullAuthenticationProtocolClaimBinding("b","endpoint_url",("c","c"))
def test_rejected_result_cannot_expose_profile():
 r=request();c=r.configuration_result
 with pytest.raises(WebullAuthenticationApprovalValidationError):WebullAuthenticationProfileApprovalResult(r.approval_id,ProfileApprovalDecision.REJECTED,False,c.configuration_id,c.profile.profile_id,c.profile,None,(),(),(),(),(),(),{}, {})
