from datetime import datetime
from decimal import Decimal

import pytest

from app.paper_trading import (PaperExecutionRequest, PaperExecutionResult, PaperTradingAccount,
                               PaperTradingDependencyError, PaperTradingPolicy, PaperTradingRuntime,
                               PaperTradingSerializationError, PaperTradingValidationError,
                               serialize_account, serialize_result)
from tests.paper_trading.helpers import Evaluator, account, request


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")])
def test_market_price_must_be_positive_finite(value):
    good = request()
    with pytest.raises(PaperTradingValidationError):
        PaperExecutionRequest(good.request_id, good.account_id, good.execution_plan_result, good.account, value,
                              good.execution_timestamp)


def test_timestamp_must_be_timezone_aware():
    good = request()
    with pytest.raises(PaperTradingValidationError):
        PaperExecutionRequest(good.request_id, good.account_id, good.execution_plan_result, good.account, 100, datetime(2026, 1, 1))


def test_policy_validation_and_round_trip():
    policy = PaperTradingPolicy(enabled=True, allow_partial_fills=True, commission_per_order="1.20")
    assert PaperTradingPolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(PaperTradingValidationError): PaperTradingPolicy(commission_per_order="-1")


def test_account_request_and_result_json_round_trips():
    state = account(); assert PaperTradingAccount.from_dict(serialize_account(state)) == state
    req = request(); assert PaperExecutionRequest.from_dict(req.to_dict()) == req
    result = PaperTradingRuntime(Evaluator(), PaperTradingPolicy(enabled=True)).execute(req)
    assert PaperExecutionResult.from_dict(serialize_result(result)) == result


def test_serializer_type_boundary():
    with pytest.raises(PaperTradingSerializationError): serialize_account({})


def test_constructor_dependency_validation():
    with pytest.raises(PaperTradingDependencyError): PaperTradingRuntime(None, PaperTradingPolicy())
    with pytest.raises(PaperTradingDependencyError): PaperTradingRuntime(Evaluator(), object())


def test_runtime_input_boundary():
    engine = PaperTradingRuntime(Evaluator(), PaperTradingPolicy(enabled=True))
    with pytest.raises(PaperTradingValidationError): engine.execute({})


def test_account_valuation_invariants():
    with pytest.raises(PaperTradingValidationError):
        PaperTradingAccount("acct", Decimal("10"), Decimal("9"), (), (), (), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("10"))
