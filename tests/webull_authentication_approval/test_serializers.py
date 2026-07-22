from app.webull_authentication_approval import *
from tests.webull_authentication_approval.fixtures import policy,request
from tests.webull_authentication_approval.test_service import approve
def test_deterministic_safe_serialization():
 r=request();result=approve(r);values=(serialize_policy(policy()),serialize_binding(r.claim_bindings[0]),serialize_request(r),serialize_result(result));rendered=repr(values);assert values==tuple(dict(x) for x in values);assert "real-password-value" not in rendered;assert "evidence_records" not in rendered;assert "configuration_id" in values[2]
def test_rejected_serialization_has_no_profile():
 result=DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy()).approve(request());data=serialize_result(result);assert data["approved_profile"] is None and data["approved_policy"] is None
