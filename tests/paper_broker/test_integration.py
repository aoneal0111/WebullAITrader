from app.broker_execution import ExecutionSafetyGate
from app.paper_broker import *
from app.trade_proposals import TradeProposalEngine
from tests.broker_execution.helpers import request as safety_request
from tests.paper_broker.helpers import request
from tests.trade_proposals.helpers import request as proposal_request
def test_full_pipeline_and_raw_rejection_immutability():
 p=TradeProposalEngine().create(proposal_request());a=ExecutionSafetyGate().authorize(safety_request(proposal=p));r=request(authorization=a);before=(p.to_dict(),a.to_dict(),r.to_dict());x=PaperBrokerAdapter().execute(r);assert x.status is PaperBrokerExecutionStatus.FILLED;assert (p.to_dict(),a.to_dict(),r.to_dict())==before
 raw=PaperBrokerAdapter().execute(p);assert raw.rejection_reason is PaperBrokerRejectionReason.INVALID_AUTHORIZATION_TYPE
