from app.webull_authentication import WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
from app.webull_authentication_approval import *
from app.webull_authentication_config import DeterministicWebullAuthenticationProfileLoader
from app.webull_protocol_evidence import *
from tests.webull_authentication_approval.fixtures import configured
from tests.webull_authentication_approval.helpers import FakeApprovedProfileConsumer
def registry_artifacts(config):
 claims=[];records=[]
 for field in required_material_fields(config):
  claim_id="claim-"+field;claims.append(WebullProtocolClaim(claim_id,"synthetic-profile",ProtocolClaimCategory.REQUEST_FIELD,field,"synthetic configured value",{"synthetic":True}));source=WebullProtocolEvidenceSource("source-"+field,EvidenceSourceClassification.SYNTHETIC_TEST,"synthetic fixture reference","synthetic fixture construction");records.append(WebullProtocolEvidenceRecord("evidence-"+field,claim_id,source,EvidenceDisposition.SUPPORTS,"synthetic configured value",True,"group-"+field))
 bundle=WebullProtocolEvidenceBundle(tuple(claims),tuple(records),{"synthetic":True});policy=WebullProtocolEvidencePolicy(enabled=True,minimum_supporting_records=1,minimum_independent_groups=1,allow_synthetic_support=True);registry=DeterministicWebullProtocolEvidenceRegistry(policy).register(bundle).registry;return tuple(registry.assess(x) for x in claims)
def test_construction_only_no_loading_registration_assessment_or_execution():
 service=DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy());assert not hasattr(service,"activate");assert not hasattr(service,"authenticate")
def test_registry_assessments_approve_then_handoff_without_execution():
 config=configured();assessments=registry_artifacts(config);bindings=tuple(WebullAuthenticationProtocolClaimBinding("binding-"+field,field,("claim-"+field,)) for field in required_material_fields(config));provenance=tuple(WebullAuthenticationAssessmentProvenance(x.claim_id,True) for x in assessments);request=WebullAuthenticationProfileApprovalRequest("approval-synthetic-integration",config,assessments,bindings,provenance);result=DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy(enabled=True,allow_synthetic_evidence=True)).approve(request);consumer=FakeApprovedProfileConsumer();profile,policy=consumer.consume(result);request_factory=WebullAuthenticationRequestFactory(profile,policy);verifier=WebullAuthenticationResponseVerifier(profile,policy);assert result.approved;assert consumer.calls==[result];assert hasattr(request_factory,"create") and hasattr(verifier,"verify")
