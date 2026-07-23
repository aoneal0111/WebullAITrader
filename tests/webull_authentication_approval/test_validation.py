import pytest
from app.webull_authentication_approval import *
from tests.webull_authentication_approval.fixtures import bindings,request
def test_valid_request():assert validate_request(request())
def test_duplicate_binding_id_field_and_assessment_raise_structural_errors():
 b=bindings();duplicate_id=type(b[1])(b[0].binding_id,b[1].profile_field,b[1].claim_ids)
 with pytest.raises(WebullAuthenticationApprovalConflictError):validate_request(request(binding_values=(b[0],duplicate_id)+b[2:]))
 duplicate_field=type(b[1])("other",b[0].profile_field,b[1].claim_ids)
 with pytest.raises(WebullAuthenticationApprovalConflictError):validate_request(request(binding_values=(b[0],duplicate_field)+b[2:]))
 a=request().evidence_assessments
 with pytest.raises(WebullAuthenticationApprovalConflictError):validate_request(request(assessments=(a[0],a[0])+a[1:]))
def test_extra_unbound_assessment_rejected_strictly():
 from tests.webull_authentication_approval.fixtures import assessment
 with pytest.raises(WebullAuthenticationApprovalAssessmentError):validate_request(request(assessments=request().evidence_assessments+(assessment("unbound"),),provenance=request().assessment_provenance))
