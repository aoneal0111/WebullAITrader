from app.webull_protocol_evidence.exceptions import *
from app.webull_protocol_evidence.models import *
def validate_bundle(bundle,existing_claim_ids=(),existing_evidence_ids=()):
 if not isinstance(bundle,WebullProtocolEvidenceBundle):raise WebullProtocolEvidenceValidationError("bundle must be WebullProtocolEvidenceBundle")
 claim_ids=[x.claim_id for x in bundle.claims];evidence_ids=[x.evidence_id for x in bundle.records]
 if len(set(claim_ids))!=len(claim_ids) or set(claim_ids)&set(existing_claim_ids):raise WebullProtocolEvidenceConflictError("duplicate claim identifier")
 if len(set(evidence_ids))!=len(evidence_ids) or set(evidence_ids)&set(existing_evidence_ids):raise WebullProtocolEvidenceConflictError("duplicate evidence identifier")
 known=set(existing_claim_ids)|set(claim_ids)
 if any(x.claim_id not in known for x in bundle.records):raise WebullProtocolEvidenceRecordError("evidence references an unknown claim")
 return bundle
def validate_claim(claim):
 if not isinstance(claim,WebullProtocolClaim):raise WebullProtocolClaimError("claim must be WebullProtocolClaim")
 return claim
