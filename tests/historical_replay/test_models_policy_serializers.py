from dataclasses import FrozenInstanceError,replace
from datetime import datetime,timedelta
from decimal import Decimal
import pytest
from app.historical_replay import *
from tests.historical_replay.helpers import event,request,runtime

def test_enum_values():
    assert len(HistoricalReplayStatus)==5 and len(HistoricalReplayEventStatus)==4 and len(HistoricalReplayOrdering)==3 and len(HistoricalReplayFailureMode)==2
def test_models_frozen_decimal_and_roundtrip():
    e=event(bid_price="99.1",available_quantity="3");assert e.market_price==Decimal("100") and e.bid_price==Decimal("99.1")
    assert HistoricalReplayEvent.from_dict(e.to_dict())==e and HistoricalReplayRequest.from_dict(request((e,)).to_dict())==request((e,))
    result=runtime()[0].replay(request((e,)));assert HistoricalReplayResult.from_dict(result.to_dict())==result
    with pytest.raises(FrozenInstanceError):e.sequence=2
def test_invalid_identity_optional_and_timestamps():
    with pytest.raises(HistoricalReplayValidationError):HistoricalReplayIdentity("","r","a")
    with pytest.raises(HistoricalReplayValidationError):HistoricalReplayIdentity("x","r","a",dataset_id="")
    with pytest.raises(HistoricalReplayValidationError):replace(event(),event_time=datetime(2026,1,1))
    req=request((event(),));
    with pytest.raises(HistoricalReplayValidationError):replace(req,completed_at=req.started_at-timedelta(seconds=1))
@pytest.mark.parametrize("field,value",[("symbol",""),("market_price","0"),("bid_price","-1"),("requested_quantity","-1"),("sequence",-1)])
def test_invalid_event_fields(field,value):
    with pytest.raises(HistoricalReplayValidationError):replace(event(),**{field:value})
def test_policy_roundtrip_and_invalid_maximum():
    p=HistoricalReplayPolicy(enabled=True,ordering=HistoricalReplayOrdering.EVENT_TIME,failure_mode=HistoricalReplayFailureMode.CONTINUE_ON_FAILURE,maximum_events=3)
    assert HistoricalReplayPolicy.from_dict(p.to_dict())==p
    with pytest.raises(HistoricalReplayValidationError):HistoricalReplayPolicy(maximum_events=0)
def test_progress_consistency():
    with pytest.raises(HistoricalReplayValidationError):HistoricalReplayProgress(2,1,0,0,0,0)
def test_serializer_boundary():
    from app.historical_replay import serialize_event
    with pytest.raises(HistoricalReplaySerializationError):serialize_event({})
