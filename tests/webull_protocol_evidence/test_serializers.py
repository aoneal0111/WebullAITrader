from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,policy,record
def test_safe_deterministic_serialization():
 b=bundle();registry=DeterministicWebullProtocolEvidenceRegistry(policy(minimum_supporting_records=1,minimum_independent_groups=1));registration=registry.register(b);assessment=registration.registry.assess(claim());values=(serialize_claim(claim()),serialize_record(record()),serialize_bundle(b),serialize_policy(policy()),serialize_registration(registration),serialize_assessment(assessment));assert values==tuple(dict(x) for x in values);assert "actual-secret" not in repr(values);assert "request"+"_body" not in repr(values)
def test_serialization_preserves_caller_order():
 b=bundle((record("evidence-2",group="g2"),record("evidence-1",group="g1")));assert [x["evidence_id"] for x in serialize_bundle(b)["records"]]==["evidence-2","evidence-1"]
