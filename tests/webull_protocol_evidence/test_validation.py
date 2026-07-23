import pytest
from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,record
def test_valid_bundle_and_claim():assert validate_bundle(bundle());assert validate_claim(claim())
def test_duplicate_claim_evidence_and_missing_reference():
 with pytest.raises(WebullProtocolEvidenceConflictError):validate_bundle(WebullProtocolEvidenceBundle((claim(),claim()),()))
 with pytest.raises(WebullProtocolEvidenceConflictError):validate_bundle(WebullProtocolEvidenceBundle((claim(),),(record(),record())))
 orphan=WebullProtocolEvidenceRecord("orphan","missing",record().source,EvidenceDisposition.SUPPORTS,"x",True,"g")
 with pytest.raises(WebullProtocolEvidenceRecordError):validate_bundle(WebullProtocolEvidenceBundle((),(orphan,)))
def test_secret_bearing_values_and_metadata_rejected_without_leak():
 with pytest.raises(WebullProtocolEvidenceValidationError) as captured:WebullProtocolClaim("c","p",ProtocolClaimCategory.SUCCESS_VALUE,"field","Bearer sentinel-secret")
 assert "sentinel-secret" not in str(captured.value)
 with pytest.raises(WebullProtocolEvidenceValidationError):WebullProtocolClaim("c","p",ProtocolClaimCategory.SUCCESS_VALUE,"field","ok",{"request"+"_body":{"x":1}})
