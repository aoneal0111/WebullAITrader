from datetime import datetime
from decimal import Decimal

import pytest

from app.execution_orchestrator import (ExecutionOrchestratorDependencyError, ExecutionOrchestratorPolicy,
                                        ExecutionOrchestratorRuntime, ExecutionOrchestratorSerializationError,
                                        ExecutionOrchestratorValidationError, PaperTradingCycleRequest,
                                        PaperTradingCycleResult, serialize_request, serialize_result)
from tests.execution_orchestrator.helpers import RuntimeSpy, real_engine, request


def test_request_and_result_round_trip():
    req = request(); assert PaperTradingCycleRequest.from_dict(serialize_request(req)) == req
    result = real_engine()[0].execute(req); assert PaperTradingCycleResult.from_dict(serialize_result(result)) == result


@pytest.mark.parametrize("price,quantity", [("0", "1"), ("-1", "1"), ("NaN", "1"), ("1", "0")])
def test_positive_numeric_boundaries(price, quantity):
    good = request()
    with pytest.raises(ExecutionOrchestratorValidationError):
        PaperTradingCycleRequest(good.request_id, good.account_id, good.portfolio, good.paper_account, price,
                                 good.execution_timestamp, quantity)


def test_aware_timestamp_required():
    good = request()
    with pytest.raises(ExecutionOrchestratorValidationError):
        PaperTradingCycleRequest(good.request_id, good.account_id, good.portfolio, good.paper_account, 1, datetime(2026, 1, 1), 1)


def test_policy_round_trip_and_validation():
    policy = ExecutionOrchestratorPolicy(enabled=True, metadata={"owner": "test"})
    assert ExecutionOrchestratorPolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(ExecutionOrchestratorValidationError): ExecutionOrchestratorPolicy(enabled=1)


def test_serializer_type_boundary():
    with pytest.raises(ExecutionOrchestratorSerializationError): serialize_request({})


def test_dependency_and_input_boundaries():
    strategy = RuntimeSpy("evaluate", lambda x: None); risk = RuntimeSpy("evaluate", lambda x: None)
    planner = RuntimeSpy("plan", lambda x: None); paper = RuntimeSpy("execute", lambda x: None)
    with pytest.raises(ExecutionOrchestratorDependencyError): ExecutionOrchestratorRuntime(None, risk, planner, paper, ExecutionOrchestratorPolicy())
    engine = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy())
    with pytest.raises(ExecutionOrchestratorValidationError): engine.execute({})


def test_account_identity_mismatch_rejected_at_input():
    good = request(); wrong = __import__("app.paper_trading", fromlist=["PaperTradingAccount"]).PaperTradingAccount("other", "1", "1", (), (), (), "0", "0", "0", "1")
    with pytest.raises(ExecutionOrchestratorValidationError):
        PaperTradingCycleRequest(good.request_id, good.account_id, good.portfolio, wrong, 1, good.execution_timestamp, 1)
