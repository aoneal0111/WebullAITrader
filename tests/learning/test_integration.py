from app.execution import PaperExecutionEngine
from app.learning import LearningEngine, LearningRequest
from app.outcomes import OutcomePolicy, OutcomeRecorder, OutcomeRequest, OutcomeStatus
from app.trade_proposals import TradeProposalEngine
from tests.execution.helpers import STAMP, execution_request
from tests.outcomes.helpers import STAMP as OUTCOME_STAMP
from tests.trade_proposals.helpers import request as proposal_request
from app.learning import LearningPolicy


def test_proposal_execution_outcome_learning_pipeline():
    proposal = TradeProposalEngine().create(proposal_request())
    execution = PaperExecutionEngine().execute(execution_request(proposal_value=proposal))
    outcome = OutcomeRecorder().record(OutcomeRequest(execution, execution.fill_price + 1,
                                                       OUTCOME_STAMP, OutcomePolicy()))
    report = LearningEngine().analyze(LearningRequest((outcome,), LearningPolicy()))
    assert outcome.status is OutcomeStatus.CLOSED
    assert report.sample_size == report.wins == 1
    assert report.net_profit == outcome.realized_pnl
