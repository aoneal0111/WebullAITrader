import hashlib
import json
from decimal import Decimal

import pytest

from app.execution import ExecutionPolicy, PaperExecutionEngine
from app.outcomes import OutcomePolicy, OutcomeRecorder, OutcomeStatus
from tests.execution.helpers import execution_request
from tests.outcomes.helpers import outcome_request


def test_long_calculations_id_metadata_and_checks():
    execution = PaperExecutionEngine().execute(execution_request(policy=ExecutionPolicy(
        commission_per_share=Decimal("0.10"))))
    request = outcome_request(execution_value=execution, exit_price=Decimal("12"), metadata={"source": "test"})
    outcome = OutcomeRecorder().record(request)
    gross = execution.fill_price * execution.filled_quantity
    assert outcome.gross_cost == gross
    assert outcome.net_cost == gross + execution.commission
    assert outcome.realized_pnl == (request.exit_price-execution.fill_price)*execution.filled_quantity-execution.commission
    assert outcome.realized_return == outcome.realized_pnl/gross
    assert outcome.status is OutcomeStatus.CLOSED
    assert [x.name for x in outcome.checks] == ["execution filled", "exit price positive", "quantity positive"]
    assert all(x.passed for x in outcome.checks)
    assert outcome.metadata["deterministic"] is True
    canonical=json.dumps({"execution_id":execution.execution_id,"exit_price":str(request.exit_price),
        "timestamp":request.timestamp.isoformat(),"policy_version":request.policy.version,
        "engine_version":"outcome_recorder_v1"},sort_keys=True,separators=(",",":"))
    assert outcome.outcome_id == hashlib.sha256(canonical.encode()).hexdigest()


def test_short_calculation_and_policy_switches():
    from tests.execution.helpers import proposal
    from app.committee import CommitteeAction
    from app.risk import RiskDecisionAction
    from tests.trade_proposals.helpers import decision
    short = proposal(risk_decision=decision(action=RiskDecisionAction.APPROVE,
                                             committee_action=CommitteeAction.BEARISH))
    execution = PaperExecutionEngine().execute(execution_request(proposal_value=short))
    request = outcome_request(execution_value=execution, exit_price=execution.fill_price-Decimal("1"),
        policy=OutcomePolicy(include_checks=False, include_execution_metadata=False))
    outcome = OutcomeRecorder().record(request)
    assert outcome.realized_pnl == execution.filled_quantity-execution.commission
    assert outcome.net_cost == outcome.gross_cost-execution.commission
    assert outcome.checks == ()
    assert "execution_metadata" not in outcome.metadata


def test_identical_inputs_identical_outputs():
    request=outcome_request()
    assert OutcomeRecorder().record(request) == OutcomeRecorder().record(request)


def test_rejected_execution_is_rejected():
    from tests.execution.helpers import proposal
    from app.risk import RiskDecisionAction
    from tests.trade_proposals.helpers import decision
    rejected_proposal = proposal(risk_decision=decision(action=RiskDecisionAction.REJECT,
        approved_notional=0, approved_risk_fraction=0))
    rejected=PaperExecutionEngine().execute(execution_request(proposal_value=rejected_proposal))
    with pytest.raises(ValueError, match="FILLED"):
        OutcomeRecorder().record(outcome_request(execution_value=rejected))
