from dataclasses import FrozenInstanceError
import pytest
from app.parameter_sweep import *
from app.parameter_sweep.serializers import serialize_request,serialize_result
from tests.parameter_sweep.helpers import request,runtime
def test_enums_defaults_and_immutable_models():
    assert len(ParameterSweepStatus)==6 and len(ParameterSweepCaseStatus)==3
    assert ParameterSweepPolicy.from_dict(ParameterSweepPolicy().to_dict())==ParameterSweepPolicy()
    result=runtime()[0].run(request(1))
    with pytest.raises(FrozenInstanceError):result.status=ParameterSweepStatus.FAILED
def test_serialization_is_stable_and_nested():
    req=request(1);result=runtime()[0].run(req);assert serialize_request(req)==req.to_dict() and serialize_result(result)==result.to_dict() and serialize_result(result)==serialize_result(result)
def test_invalid_models_and_serializer():
    with pytest.raises(ParameterSweepValidationError):ParameterSweepIdentity("")
    with pytest.raises(ParameterSweepValidationError):ParameterSweepPolicy(enabled=1)
    with pytest.raises(ParameterSweepValidationError):ParameterSweepSummary(1,1,1,1,0)
    with pytest.raises(ParameterSweepSerializationError):serialize_result({})
