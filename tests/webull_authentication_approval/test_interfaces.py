from app.webull_authentication_approval import DeterministicWebullAuthenticationProfileApprovalService,WebullAuthenticationProfileApprovalService
def test_exact_service_interface():
 assert {n for n in WebullAuthenticationProfileApprovalService.__dict__ if not n.startswith("_")}=={"approve"};assert {n for n in DeterministicWebullAuthenticationProfileApprovalService.__dict__ if not n.startswith("_")}=={"approve"}
