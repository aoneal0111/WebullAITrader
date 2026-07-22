from dataclasses import replace
import pytest
from app.historical_replay import *
from tests.historical_replay.helpers import Coordinator,event,request,runtime

def test_disabled_zero_calls_shape_preserves_initial_state():
    c=Coordinator();req=request((event(),));result=HistoricalReplayRuntime(c,HistoricalReplayPolicy()).replay(req)
    assert result.status is HistoricalReplayStatus.DISABLED and result.disabled and result.event_results==() and result.progress.total_events==1
    assert result.progress.processed_events==0 and result.final_state is req.initial_paper_account and c.calls==[]
def test_empty_allowed_and_disallowed():
    engine,c=runtime(allow_empty_events=True);result=engine.replay(request())
    assert result.status is HistoricalReplayStatus.EMPTY and result.final_state==request().initial_paper_account and c.calls==[]
    engine,c=runtime()
    with pytest.raises(HistoricalReplayValidationError):engine.replay(request())
    assert c.calls==[]
def test_duplicates_and_maximum_are_global_zero_call_failures():
    e=event();cases=((e,replace(e,orchestrator_request_id="other")),(e,replace(e,event_id="other",orchestrator_request_id="other")))
    for events in cases:
        engine,c=runtime()
        with pytest.raises(HistoricalReplayValidationError):engine.replay(request(events))
        assert c.calls==[]
    engine,c=runtime(maximum_events=1)
    with pytest.raises(HistoricalReplayValidationError):engine.replay(request((event(0),event(1))))
    assert c.calls==[]
def test_duplicates_allowed_by_policy():
    e=event();other=replace(e,orchestrator_request_id="other")
    result=runtime(allow_duplicate_event_ids=True,allow_duplicate_sequences=True)[0].replay(request((e,other)))
    assert result.progress.processed_events==2
def test_missing_requested_quantity_structural_zero_call():
    engine,c=runtime()
    with pytest.raises(HistoricalReplayValidationError):engine.replay(request((event(quantity=None),)))
    assert c.calls==[]
