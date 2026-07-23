import pytest
from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,policy,record
def test_disabled_default_and_invalid_dependency():
 registry=DeterministicWebullProtocolEvidenceRegistry(WebullProtocolEvidencePolicy())
 with pytest.raises(WebullProtocolEvidenceDisabledError):registry.register(bundle())
 with pytest.raises(WebullProtocolEvidenceDependencyError):DeterministicWebullProtocolEvidenceRegistry(object())
def test_registration_returns_new_immutable_state_and_preserves_order():
 registry=DeterministicWebullProtocolEvidenceRegistry(policy(minimum_supporting_records=1,minimum_independent_groups=1));result=registry.register(bundle());assert result.added_claim_ids==("claim-auth-endpoint",);assert result.added_evidence_ids==("evidence-controlled-observation-1",);assert result.total_claims==result.total_records==1
 with pytest.raises(WebullProtocolEvidenceAssessmentError):registry.assess(claim())
 assert result.registry.assess(claim()).decision is EvidenceDecision.SUPPORTED
def test_cross_registration_duplicate_conflicts():
 first=DeterministicWebullProtocolEvidenceRegistry(policy()).register(bundle()).registry
 with pytest.raises(WebullProtocolEvidenceConflictError):first.register(bundle())
def test_equivalent_inputs_deterministic():
 r1=DeterministicWebullProtocolEvidenceRegistry(policy(minimum_supporting_records=1,minimum_independent_groups=1)).register(bundle());r2=DeterministicWebullProtocolEvidenceRegistry(policy(minimum_supporting_records=1,minimum_independent_groups=1)).register(WebullProtocolEvidenceBundle.from_dict(bundle().to_dict()));assert r1.to_dict()==r2.to_dict();assert r1.registry.assess(claim())==r2.registry.assess(claim())
