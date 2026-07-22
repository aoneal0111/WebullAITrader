from dataclasses import FrozenInstanceError
import pytest
from app.webull_protocol_evidence import *
from tests.webull_protocol_evidence.fixtures import bundle,claim,record,source
def test_claim_source_record_bundle_frozen_immutable_roundtrip():
 values=(claim(),source(),record(),bundle());assert WebullProtocolClaim.from_dict(values[0].to_dict())==values[0];assert WebullProtocolEvidenceSource.from_dict(values[1].to_dict())==values[1];assert WebullProtocolEvidenceRecord.from_dict(values[2].to_dict())==values[2];assert WebullProtocolEvidenceBundle.from_dict(values[3].to_dict())==values[3]
 assert all(not hasattr(x,"__dict__") for x in values)
 with pytest.raises(FrozenInstanceError):values[0].claim_id="x"
 with pytest.raises(TypeError):values[2].metadata["x"]=1
def test_source_embedded_credentials_rejected():
 with pytest.raises(WebullProtocolEvidenceRecordError):WebullProtocolEvidenceSource("source",EvidenceSourceClassification.THIRD_PARTY_REPORT,"https://user:sentinel@mock.invalid/reference","synthetic")
