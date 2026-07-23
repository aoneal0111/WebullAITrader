from decimal import Decimal
from app.execution import ExecutionPolicy,ExecutionStatus,PaperExecutionEngine,PaperExecutionRequest
from app.trade_proposals import TradeProposalEngine
from tests.execution.helpers import STAMP
from tests.trade_proposals.helpers import request

def test_trade_proposal_engine_to_execution_engine():
    proposal=TradeProposalEngine().create(request())
    result=PaperExecutionEngine().execute(PaperExecutionRequest(proposal,STAMP,ExecutionPolicy()))
    assert result.status is ExecutionStatus.FILLED and result.proposal_id==proposal.proposal_id
    assert result.proposal_engine_version==proposal.proposal_engine_version

def test_execution_package_has_no_broker_or_webull_references():
    from pathlib import Path
    root=Path("app/execution")
    source="\n".join(path.read_text(encoding="utf-8").lower() for path in root.glob("*.py"))
    assert "app.broker" not in source and "app.webull" not in source
