from app.webull_authentication_approval import *
from app.webull_authentication_config import DeterministicWebullAuthenticationProfileLoader
from app.webull_protocol_evidence import EvidenceDecision,WebullProtocolEvidenceAssessment
from tests.webull_authentication_config.fixtures import configuration
def configured(**changes):return DeterministicWebullAuthenticationProfileLoader().load(configuration(**changes))
def assessment(claim_id,decision=EvidenceDecision.SUPPORTED,eligible=True):return WebullProtocolEvidenceAssessment(claim_id,decision,eligible,("evidence-"+claim_id,),(),(),1,1,{"enabled":True,"supporting_records":eligible,"independent_groups":eligible,"reproducible_support":True,"synthetic_support":True,"contradiction_free":decision is not EvidenceDecision.CONTRADICTED},{"synthetic_fixture":True})
def bindings(config=None):return tuple(WebullAuthenticationProtocolClaimBinding("binding-"+field,field,("claim-"+field,),True,{"synthetic":True}) for field in required_material_fields(config or configured()))
def request(assessments=None,binding_values=None,provenance=None,config=None):
 c=config or configured();b=binding_values or bindings(c);a=assessments if assessments is not None else tuple(assessment("claim-"+x.profile_field) for x in b);p=provenance if provenance is not None else tuple(WebullAuthenticationAssessmentProvenance(x.claim_id,False) for x in a);return WebullAuthenticationProfileApprovalRequest("approval-synthetic-1",c,tuple(a),tuple(b),tuple(p),{"synthetic":True})
def policy(**changes):
 values=dict(enabled=True);values.update(changes);return WebullAuthenticationProfileApprovalPolicy(**values)
