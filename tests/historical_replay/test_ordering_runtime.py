from dataclasses import replace
from datetime import timedelta
import pytest
from app.historical_replay import *
from tests.execution_orchestrator.helpers import NOW
from tests.historical_replay.helpers import Coordinator,event,request,runtime

def ids(result):return tuple(x.event_id for x in result.event_results)
def test_input_order_preserved_and_request_unchanged():
    events=(event(2),event(0),event(1));req=request(events);before=req.to_dict();result=runtime(ordering=HistoricalReplayOrdering.INPUT_ORDER)[0].replay(req)
    assert ids(result)==("event-2","event-0","event-1") and req.to_dict()==before
def test_event_time_stable_equal_time():
    same=NOW;events=(event(2,event_time=same),event(0,event_time=same-timedelta(minutes=1)),event(1,event_time=same))
    result=runtime(ordering=HistoricalReplayOrdering.EVENT_TIME)[0].replay(request(events));assert ids(result)==("event-0","event-2","event-1")
def test_event_time_then_sequence_and_complete_tie_stability():
    events=(event(2,event_time=NOW,sequence=5),event(0,event_time=NOW,sequence=1),event(1,event_time=NOW,sequence=1))
    result=runtime(ordering=HistoricalReplayOrdering.EVENT_TIME_THEN_SEQUENCE,allow_duplicate_sequences=True)[0].replay(request(events));assert ids(result)==("event-0","event-1","event-2")
def test_once_per_event_and_deterministic_repeated_runs():
    events=(event(0),event(1));engine,coordinator=runtime();a=engine.replay(request(events));assert len(coordinator.calls)==2
    b=runtime()[0].replay(request(events));assert a==b and a.to_dict()==b.to_dict()
def test_state_continuity_and_final_state():
    engine,coordinator=runtime();result=engine.replay(request((event(0),event(1))))
    assert coordinator.calls[0].paper_account.cash==10000
    assert coordinator.calls[1].paper_account==result.event_results[0].resulting_state
    assert result.final_state==result.event_results[-1].resulting_state and result.final_state.cash==9800
def test_identity_and_timestamp_exactly_copied():
    e=event();result=runtime()[0].replay(request((e,))).event_results[0]
    assert result.event_id==e.event_id and result.sequence==e.sequence and result.symbol==e.symbol and result.event_time is e.event_time
