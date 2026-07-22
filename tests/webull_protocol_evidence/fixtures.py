from app.webull_protocol_evidence import *
def claim(value="https://mock.invalid/authenticate"):return WebullProtocolClaim("claim-auth-endpoint","synthetic-profile",ProtocolClaimCategory.ENDPOINT,"authentication endpoint",value,{"synthetic":True})
def source(identifier="source-controlled-1",classification=EvidenceSourceClassification.CONTROLLED_OBSERVATION):return WebullProtocolEvidenceSource(identifier,classification,"synthetic caller reference","caller-supplied fixture observation",{"synthetic":True})
def record(identifier="evidence-controlled-observation-1",disposition=EvidenceDisposition.SUPPORTS,group="independent-1",classification=EvidenceSourceClassification.CONTROLLED_OBSERVATION,reproducible=True,observed="https://mock.invalid/authenticate"):return WebullProtocolEvidenceRecord(identifier,"claim-auth-endpoint",source("source-"+identifier,classification),disposition,observed,reproducible,group,{"synthetic_fixture":True})
def bundle(records=None):return WebullProtocolEvidenceBundle((claim(),),tuple(records or (record(),)),{"synthetic":True})
def policy(**changes):
 values=dict(enabled=True,minimum_supporting_records=2,minimum_independent_groups=2,reject_any_contradiction=True,require_reproducible_support=True,allow_synthetic_support=False);values.update(changes);return WebullProtocolEvidencePolicy(**values)
