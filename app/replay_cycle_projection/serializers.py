from app.replay_cycle_projection.exceptions import ReplayCycleProjectionSerializationError
from app.replay_cycle_projection.models import *
from app.replay_cycle_projection.policies import ReplayCycleProjectionPolicy
def _s(v,t):
    if not isinstance(v,t):raise ReplayCycleProjectionSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_request=lambda v:_s(v,ReplayCycleProjectionRequest)
serialize_item_result=lambda v:_s(v,ReplayCycleProjectionItemResult)
serialize_progress=lambda v:_s(v,ReplayCycleProjectionProgress)
serialize_criteria=lambda v:_s(v,ReplayCycleProjectionCriteriaResult)
serialize_result=lambda v:_s(v,ReplayCycleProjectionResult)
serialize_policy=lambda v:_s(v,ReplayCycleProjectionPolicy)
