from dataclasses import FrozenInstanceError
import pytest
from app.backtest_report import *
from app.backtest_report.serializers import serialize_request,serialize_result
from tests.backtest_report.helpers import request
def test_enums_policy_and_immutability():
    assert len(BacktestReportStatus)==6 and BacktestReportPolicy.from_dict(BacktestReportPolicy().to_dict())==BacktestReportPolicy()
    result=BacktestReportRuntime().create(request())
    with pytest.raises(FrozenInstanceError):result.status=BacktestReportStatus.FAILED
def test_serialization_is_stable_and_nested():
    req=request();result=BacktestReportRuntime().create(req)
    assert serialize_request(req)==req.to_dict() and serialize_result(result)==result.to_dict() and serialize_result(result)==serialize_result(result)
def test_invalid_identity_policy_serializer():
    with pytest.raises(BacktestReportValidationError):BacktestReportIdentity("","run")
    with pytest.raises(BacktestReportValidationError):BacktestReportPolicy(enabled="yes")
    with pytest.raises(BacktestReportSerializationError):serialize_result({})
