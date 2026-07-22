from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,policy,record
def assess(records,**rules):return DeterministicWebullProtocolEvidenceRegistry(policy(**rules)).register(bundle(records)).registry.assess(claim())
def test_support_requires_record_and_independent_group_thresholds():
 one=assess((record(),));assert one.decision is EvidenceDecision.INSUFFICIENT and not one.eligible_for_profile_use
 two=assess((record(),record("evidence-independent-observation-2",group="independent-2")));assert two.decision is EvidenceDecision.SUPPORTED and two.eligible_for_profile_use;assert two.independent_support_group_count==2
def test_contradiction_explicitly_blocks():
 result=assess((record(),record("e2",group="g2"),record("e3",EvidenceDisposition.CONTRADICTS,"g3")));assert result.decision is EvidenceDecision.CONTRADICTED;assert not result.eligible_for_profile_use;assert result.contradicting_record_ids==("e3",)
def test_inconclusive_record_reported_not_supporting():
 result=assess((record("e1",EvidenceDisposition.INCONCLUSIVE),),minimum_supporting_records=1,minimum_independent_groups=1);assert result.decision is EvidenceDecision.INSUFFICIENT;assert result.inconclusive_record_ids==("e1",)
def test_reproducibility_and_synthetic_restrictions():
 nonrep=assess((record(reproducible=False),),minimum_supporting_records=1,minimum_independent_groups=1);assert not nonrep.eligible_for_profile_use
 synthetic=record(classification=EvidenceSourceClassification.SYNTHETIC_TEST);blocked=assess((synthetic,),minimum_supporting_records=1,minimum_independent_groups=1);allowed=assess((synthetic,),minimum_supporting_records=1,minimum_independent_groups=1,allow_synthetic_support=True);assert not blocked.eligible_for_profile_use;assert allowed.eligible_for_profile_use
def test_disabled_assessment_is_explicit():
 b=bundle();registry=DeterministicWebullProtocolEvidenceRegistry(WebullProtocolEvidencePolicy(),b.claims,b.records);result=registry.assess(claim());assert result.decision is EvidenceDecision.DISABLED;assert not result.eligible_for_profile_use
