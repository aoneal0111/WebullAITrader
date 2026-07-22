from dataclasses import replace
import pytest
from app.historical_replay import HistoricalReplayStatus
from app.replay_cycle_projection import *
from tests.replay_cycle_projection.helpers import BuilderSpy,failed_replay,projection,replay
def test_disabled_is_zero_call_and_preserves_source():
    source=replay();runtime,builder,request=projection(source,enabled=False);result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.DISABLED and result.disabled and result.replay_result is source and builder.calls==[]
def test_empty_allowed_and_disallowed_zero_calls():
    source=failed_replay()
    runtime,builder,request=projection(source,allow_empty=True);assert runtime.project(request).status is ReplayCycleProjectionStatus.EMPTY and builder.calls==[]
    runtime,builder,request=projection(source);assert runtime.project(request).status is ReplayCycleProjectionStatus.REJECTED and builder.calls==[]
def test_wrong_request_and_dependency_rejected():
    with pytest.raises(ReplayCycleProjectionDependencyError):ReplayCycleProjectionRuntime(None,ReplayCycleProjectionPolicy())
    runtime=ReplayCycleProjectionRuntime(BuilderSpy(),ReplayCycleProjectionPolicy())
    with pytest.raises(ReplayCycleProjectionValidationError):runtime.project(object())
def test_disabled_replay_is_structurally_rejected_before_build():
    source=replay();source=replace(source,status=HistoricalReplayStatus.DISABLED,event_results=(),disabled=True)
    runtime,builder,request=projection(source)
    with pytest.raises(ReplayCycleProjectionValidationError):runtime.project(request)
    assert builder.calls==[]
