import pytest
from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,policy
def test_public_errors_are_normalized_and_payload_safe():
 registry=DeterministicWebullProtocolEvidenceRegistry(policy())
 with pytest.raises(WebullProtocolEvidenceValidationError):registry.register(object())
 with pytest.raises(WebullProtocolClaimError):registry.assess(object())
 sensitive="actual-secret-sentinel"
 with pytest.raises(WebullProtocolEvidenceValidationError) as captured:WebullProtocolClaim("c","p",ProtocolClaimCategory.SUCCESS_VALUE,"field",sensitive)
 assert sensitive not in str(captured.value)
def test_conflicting_claim_identity_rejected():
 registered=DeterministicWebullProtocolEvidenceRegistry(policy()).register(bundle()).registry
 conflict=WebullProtocolClaim(claim().claim_id,"other-scope",claim().category,claim().subject,claim().asserted_value)
 with pytest.raises(WebullProtocolEvidenceConflictError):registered.assess(conflict)
