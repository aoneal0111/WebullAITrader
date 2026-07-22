from app.webull_protocol_evidence.assessment import assess_claim
from app.webull_protocol_evidence.exceptions import *
from app.webull_protocol_evidence.models import *
from app.webull_protocol_evidence.policies import WebullProtocolEvidencePolicy
from app.webull_protocol_evidence.validation import validate_bundle,validate_claim
@dataclass(frozen=True,slots=True)
class DeterministicWebullProtocolEvidenceRegistry:
 _policy:WebullProtocolEvidencePolicy;_claims:tuple=();_records:tuple=()
 def __post_init__(self):
  if not isinstance(self._policy,WebullProtocolEvidencePolicy):raise WebullProtocolEvidenceDependencyError("policy must be WebullProtocolEvidencePolicy")
  if not isinstance(self._claims,tuple) or not isinstance(self._records,tuple):raise WebullProtocolEvidenceDependencyError("registry state must be immutable tuples")
 def register(self,evidence_bundle):
  if not self._policy.enabled:raise WebullProtocolEvidenceDisabledError("protocol evidence registry is disabled")
  bundle=validate_bundle(evidence_bundle,tuple(x.claim_id for x in self._claims),tuple(x.evidence_id for x in self._records))
  registry=DeterministicWebullProtocolEvidenceRegistry(self._policy,self._claims+bundle.claims,self._records+bundle.records)
  return WebullProtocolEvidenceRegistrationResult(registry,tuple(x.claim_id for x in bundle.claims),tuple(x.evidence_id for x in bundle.records),len(registry._claims),len(registry._records),{"deterministic":True})
 def assess(self,protocol_claim):
  claim=validate_claim(protocol_claim)
  existing=next((x for x in self._claims if x.claim_id==claim.claim_id),None)
  if existing is None:raise WebullProtocolEvidenceAssessmentError("claim is not registered")
  if existing!=claim:raise WebullProtocolEvidenceConflictError("claim identity conflicts with registered claim")
  return assess_claim(claim,self._records,self._policy)
from dataclasses import dataclass
