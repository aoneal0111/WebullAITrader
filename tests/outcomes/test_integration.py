from app.execution import PaperExecutionEngine
from app.outcomes import OutcomeRecorder, OutcomeStatus
from tests.execution.helpers import execution_request
from tests.outcomes.helpers import outcome_request


def test_paper_execution_to_outcome_preserves_inputs():
    execution = PaperExecutionEngine().execute(execution_request())
    original_execution = execution.to_dict()
    proposal = execution_request().proposal
    original_proposal = proposal.to_dict()
    outcome = OutcomeRecorder().record(outcome_request(execution_value=execution))
    assert outcome.status is OutcomeStatus.CLOSED
    assert outcome.execution_id == execution.execution_id
    assert execution.to_dict() == original_execution
    assert proposal.to_dict() == original_proposal
