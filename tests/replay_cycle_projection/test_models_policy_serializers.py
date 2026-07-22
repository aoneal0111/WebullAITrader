from dataclasses import FrozenInstanceError
import pytest
from app.replay_cycle_projection import *
from app.replay_cycle_projection.serializers import serialize_policy,serialize_result
from tests.replay_cycle_projection.helpers import projection
def test_enum_values_and_policy_roundtrip():
    assert len(ReplayCycleProjectionStatus)==6 and len(ReplayCycleProjectionItemStatus)==5 and len(ReplayCycleProjectionFailureMode)==2
    policy=ReplayCycleProjectionPolicy(failure_mode=ReplayCycleProjectionFailureMode.CONTINUE_ON_FAILURE,allow_empty=True)
    assert ReplayCycleProjectionPolicy.from_dict(policy.to_dict())==policy and serialize_policy(policy)==policy.to_dict()
def test_models_are_frozen_and_result_roundtrips():
    runtime,builder,request=projection();result=runtime.project(request)
    assert ReplayCycleProjectionResult.from_dict(result.to_dict())==result and serialize_result(result)==result.to_dict()
    with pytest.raises(FrozenInstanceError):result.status=ReplayCycleProjectionStatus.FAILED
def test_invalid_policy_and_serializer_type():
    with pytest.raises(ReplayCycleProjectionValidationError):ReplayCycleProjectionPolicy(failure_mode="STOP_ON_FAILURE")
    with pytest.raises(ReplayCycleProjectionSerializationError):serialize_result({})
def test_progress_consistency_validation():
    with pytest.raises(ReplayCycleProjectionValidationError):ReplayCycleProjectionProgress(2,1,1,0,0,0,0)
