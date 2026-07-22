from app.broker_execution import *
from app.trade_proposals import TradeProposalEngine
from tests.trade_proposals.helpers import request as proposal_request
from tests.broker_execution.helpers import request
def test_proposal_to_gate_does_not_mutate_proposal():
    p=TradeProposalEngine().create(proposal_request());before=p.to_dict();a=ExecutionSafetyGate().authorize(request(proposal=p));assert a.decision is SafetyDecision.APPROVED;assert p.to_dict()==before
