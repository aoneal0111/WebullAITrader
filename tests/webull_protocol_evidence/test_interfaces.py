from app.webull_protocol_evidence import DeterministicWebullProtocolEvidenceRegistry,WebullProtocolEvidenceRegistry
def test_exact_registry_interface():
 assert {n for n in WebullProtocolEvidenceRegistry.__dict__ if not n.startswith("_")}=={"register","assess"};assert {n for n in DeterministicWebullProtocolEvidenceRegistry.__dict__ if not n.startswith("_")}=={"register","assess"}
