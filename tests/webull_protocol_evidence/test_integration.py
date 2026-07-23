from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,policy,record
from tests.webull_protocol_evidence.helpers import FakeProfileApprovalBoundary
def test_construction_has_no_automatic_registration_or_assessment():
 registry=DeterministicWebullProtocolEvidenceRegistry(policy());assert not hasattr(registry,"fetch");assert not hasattr(registry,"refresh")
def test_eligible_assessment_handoff_is_explicit_only():
 records=(record(),record("evidence-independent-observation-2",group="independent-2"));registry=DeterministicWebullProtocolEvidenceRegistry(policy()).register(bundle(records)).registry;assessment=registry.assess(claim());approval=FakeProfileApprovalBoundary();assert approval.consider(assessment);assert approval.calls==[assessment];assert assessment.metadata["eligibility_scope"]=="policy-criteria-only"
def test_synthetic_fixture_cannot_approve_by_default():
 synthetic=record(classification=EvidenceSourceClassification.SYNTHETIC_TEST);registry=DeterministicWebullProtocolEvidenceRegistry(policy(minimum_supporting_records=1,minimum_independent_groups=1)).register(bundle((synthetic,))).registry;assert not FakeProfileApprovalBoundary().consider(registry.assess(claim()))
