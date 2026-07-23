from dataclasses import FrozenInstanceError,replace
import pytest
from app.experiment import *
from app.experiment.serializers import serialize_request,serialize_result
from tests.experiment.helpers import request,runtime
def test_enums_defaults_and_immutable_models():
    assert len(ExperimentStatus)==6 and len(ExperimentSweepStatus)==3
    assert ExperimentPolicy.from_dict(ExperimentPolicy().to_dict())==ExperimentPolicy()
    result=runtime()[0].run(request(1))
    with pytest.raises(FrozenInstanceError):result.status=ExperimentStatus.FAILED
    req=request(1)
    for model,field,value in ((req.identity,"experiment_id","changed"),(req.sweeps[0].identity,"sweep_entry_id","changed"),(req.sweeps[0],"identity",req.sweeps[0].identity),(req,"sweeps",()),(result.sweeps[0],"index",4),(result.summary,"total_sweeps",4)):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
    assert isinstance(req.sweeps,tuple) and isinstance(result.sweeps,tuple) and isinstance(result.errors,tuple)
def test_serialization_is_stable_and_nested():
    req=request(1);result=runtime()[0].run(req)
    serialized=serialize_result(result)
    assert serialize_request(req)==req.to_dict() and serialized==result.to_dict() and serialized==serialize_result(result)
    assert serialized["status"]=="COMPLETED" and serialized["requested_at"]==req.requested_at.isoformat()
    record=serialized["sweeps"][0]
    assert record["parameter_sweep_request"]==req.sweeps[0].parameter_sweep_request.to_dict()
    assert record["parameter_sweep_result"]==result.sweeps[0].parameter_sweep_result.to_dict()
    assert not ({"sweep_request","sweep_result","experiment_sweep_id"}&set(record))
def test_failed_record_serializes_none_and_stable_fields():
    from tests.experiment.helpers import Executor
    result=runtime(Executor(callback=lambda ignored:object()))[0].run(request(1));record=serialize_result(result)["sweeps"][0]
    assert record["parameter_sweep_result"] is None and record["error_type"]=="InvalidParameterSweepResult"
def test_expected_functions_are_public_package_exports():
    import app.experiment as package
    expected=("serialize_policy","serialize_identity","serialize_sweep_identity","serialize_sweep_request","serialize_request","serialize_criteria","serialize_sweep_record","serialize_summary","serialize_result","validate_request")
    assert all(name in package.__all__ and callable(getattr(package,name)) for name in expected)
def test_invalid_models_and_serializer():
    with pytest.raises(ExperimentValidationError):ExperimentIdentity("")
    with pytest.raises(ExperimentValidationError):ExperimentPolicy(enabled=1)
    with pytest.raises(ExperimentValidationError):ExperimentSummary(1,1,1,1,0)
    req=request(1)
    with pytest.raises(ExperimentValidationError):ExperimentSweepRecord(-1,req.sweeps[0].identity,ExperimentSweepStatus.COMPLETED,req.sweeps[0].parameter_sweep_request,None)
    with pytest.raises(ExperimentValidationError):replace(req.sweeps[0],parameter_sweep_request=object())
    result=runtime()[0].run(req)
    with pytest.raises(ExperimentValidationError):replace(result.sweeps[0],parameter_sweep_result=object())
    with pytest.raises(ExperimentSerializationError):serialize_result({})
