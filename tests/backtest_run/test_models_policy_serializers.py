from dataclasses import FrozenInstanceError
import pytest
from app.backtest_run import *
from app.backtest_run.serializers import serialize_request,serialize_result
from tests.backtest_run.helpers import request,runtime
def test_enum_values_and_policy():
    assert len(BacktestRunStatus)==6 and len(BacktestRunStage)==6 and len(BacktestRunStageStatus)==4
    assert BacktestRunPolicy.from_dict(BacktestRunPolicy().to_dict())==BacktestRunPolicy()
def test_immutable_models_and_serialization():
    req=request(1);result=runtime()[0].run(req)
    assert serialize_request(req)==req.to_dict() and serialize_result(result)==result.to_dict()
    with pytest.raises(FrozenInstanceError):result.status=BacktestRunStatus.FAILED
def test_invalid_identity_policy_and_time():
    with pytest.raises(BacktestRunValidationError):BacktestRunIdentity("")
    with pytest.raises(BacktestRunValidationError):BacktestRunIdentity("run","")
    with pytest.raises(BacktestRunValidationError):BacktestRunPolicy(enabled="yes")
def test_stable_stage_order():
    result=runtime()[0].run(request(1));assert tuple(x.stage for x in result.stage_results)==tuple(BacktestRunStage)
