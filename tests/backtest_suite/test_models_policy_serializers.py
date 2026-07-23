from dataclasses import FrozenInstanceError
import pytest
from app.backtest_suite import *
from app.backtest_suite.serializers import serialize_request,serialize_result
from tests.backtest_suite.helpers import request,runtime
def test_enums_policy_and_immutability():
    assert len(BacktestSuiteStatus)==6 and len(BacktestSuiteItemStatus)==6
    assert BacktestSuitePolicy.from_dict(BacktestSuitePolicy().to_dict())==BacktestSuitePolicy()
    result=runtime()[0].run(request(1));
    with pytest.raises(FrozenInstanceError):result.status=BacktestSuiteStatus.FAILED
def test_serialization_stable_and_nested():
    req=request(1);result=runtime()[0].run(req);assert serialize_request(req)==req.to_dict() and serialize_result(result)==result.to_dict() and serialize_result(result)==serialize_result(result)
def test_invalid_identity_policy_summary_serializer():
    with pytest.raises(BacktestSuiteValidationError):BacktestSuiteIdentity("")
    with pytest.raises(BacktestSuiteValidationError):BacktestSuitePolicy(enabled=1)
    with pytest.raises(BacktestSuiteValidationError):BacktestSuiteSummary(1,1,1,1,0)
    with pytest.raises(BacktestSuiteSerializationError):serialize_result({})
