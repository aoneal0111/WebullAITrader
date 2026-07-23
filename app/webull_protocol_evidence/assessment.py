from app.webull_protocol_evidence.models import *
def assess_claim(claim,records,policy):
 related=tuple(x for x in records if x.claim_id==claim.claim_id);support=tuple(x for x in related if x.disposition is EvidenceDisposition.SUPPORTS);against=tuple(x for x in related if x.disposition is EvidenceDisposition.CONTRADICTS);unclear=tuple(x for x in related if x.disposition is EvidenceDisposition.INCONCLUSIVE)
 qualifying=tuple(x for x in support if (policy.allow_synthetic_support or x.source.classification is not EvidenceSourceClassification.SYNTHETIC_TEST) and (not policy.require_reproducible_support or x.reproducible));groups=len({x.independence_group for x in qualifying});repro=sum(x.reproducible for x in support)
 criteria={"enabled":policy.enabled,"supporting_records":len(qualifying)>=policy.minimum_supporting_records,"independent_groups":groups>=policy.minimum_independent_groups,"reproducible_support":not policy.require_reproducible_support or all(x.reproducible for x in qualifying),"synthetic_support":policy.allow_synthetic_support or all(x.source.classification is not EvidenceSourceClassification.SYNTHETIC_TEST for x in qualifying),"contradiction_free":not against}
 if not policy.enabled:decision=EvidenceDecision.DISABLED
 elif against and policy.reject_any_contradiction:decision=EvidenceDecision.CONTRADICTED
 elif all((criteria["supporting_records"],criteria["independent_groups"],criteria["reproducible_support"],criteria["synthetic_support"])):decision=EvidenceDecision.SUPPORTED
 else:decision=EvidenceDecision.INSUFFICIENT
 eligible=decision is EvidenceDecision.SUPPORTED and (not policy.reject_any_contradiction or not against)
 return WebullProtocolEvidenceAssessment(claim.claim_id,decision,eligible,tuple(x.evidence_id for x in support),tuple(x.evidence_id for x in against),tuple(x.evidence_id for x in unclear),groups,repro,criteria,{"eligibility_scope":"policy-criteria-only"})
