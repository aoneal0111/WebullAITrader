from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.execution import ExecutionPolicy, PaperExecutionRequest
from app.trade_proposals import TradeProposalEngine
from tests.trade_proposals.helpers import request as proposal_request

STAMP = datetime(2026, 7, 21, 20, 2, tzinfo=UTC)

def proposal(**changes):
    return TradeProposalEngine().create(proposal_request(**changes))

def execution_request(policy=None, proposal_value=None, **changes):
    values = {"proposal": proposal_value or proposal(), "timestamp": STAMP,
              "policy": policy or ExecutionPolicy()}
    values.update(changes)
    return PaperExecutionRequest(**values)
