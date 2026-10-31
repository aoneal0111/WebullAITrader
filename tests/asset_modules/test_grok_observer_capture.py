from dataclasses import replace
from datetime import timedelta
import json
from queue import Full
from unittest.mock import patch

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_observer_capture import ContextCapture,load_capture,observe_capture,webull_quote_snapshot
from tests.asset_modules.test_grok_observer import snap,fixture,NOW,BASE
from tests.asset_modules.test_grok_observer_context import evidence


def test_two_owned_contexts_round_trip_without_execution_or_network(tmp_path):
    directory=tmp_path/'capture'
    with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')):
        capture=ContextCapture(directory,BASE,clock=lambda:NOW)
        try:
            for engine in (EngineId.WARRIOR,EngineId.SCALPER):
                s=snap(engine);assert capture.submit(s,evidence(s))
        finally:summary=capture.close()
    assert summary['writer_stopped'] and not summary['writer_failed']
    assert summary['counts']['enqueued']==summary['counts']['written']==2
    assert summary['records_by_engine']=={'WARRIOR_MOMENTUM_V1':1,'QUICK_SCALPER':1}
    path=directory/'private-observer-context.jsonl';before=path.read_bytes()
    records=load_capture(path,BASE)
    assert len(records)==2 and records[0][0].engine!=records[1][0].engine
    trial,requests,_=fixture()
    results=observe_capture(path,trial,BASE)
    assert len(requests)==2 and all(r.status=='OBSERVED' and not r.execution_authorized for r in results)
    assert path.read_bytes()==before and not summary['online_observer_ready']


def test_canonical_webull_aggregate_time_is_labeled_without_retiming(tmp_path):
    row=dict(price='100',bid='100',ask='100.1',last_trade_time=NOW.isoformat(),quote_time=(NOW-timedelta(seconds=1)).isoformat())
    s,basis=webull_quote_snapshot(engine=EngineId.WARRIOR,event_id='actual-schema',symbol='AAPL',row=row,captured_at=NOW,baseline=BASE)
    assert s.bid_at==s.ask_at==NOW-timedelta(seconds=1)
    assert basis=='WEBULL_PROVIDER_QUOTE_TIME_SHARED'
    # Use history ending before the actual provider quote, never retime the quote.
    context=evidence(s)
    context=replace(context,bars=tuple(replace(b,closed_at=b.closed_at-timedelta(minutes=1),available_at=b.available_at-timedelta(minutes=1)) for b in context.bars))
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    try:
        assert not capture.submit(s,context) # provenance cannot be silently omitted
        assert capture.submit(s,context,quote_basis=basis)
    finally:summary=capture.close()
    path=tmp_path/'capture'/'private-observer-context.jsonl'
    assert json.loads(path.read_text())['quote_timestamp_basis']==basis
    loaded=load_capture(path,BASE)
    assert loaded[0][0].bid_at==s.bid_at and summary['counts']['written']==1


@pytest.mark.parametrize('case',['stale','future','baseline','owner','gap'])
def test_bad_evidence_is_refused_without_propagating_to_engine(tmp_path,case):
    s=snap();context=evidence(s)
    if case=='stale':s=replace(s,bid_at=NOW-timedelta(seconds=3))
    elif case=='future':s=replace(s,ask_at=NOW+timedelta(seconds=1))
    elif case=='baseline':s=replace(s,baseline_sha256='b'*64)
    elif case=='owner':context=replace(context,engine=EngineId.SCALPER)
    else:context=replace(context,bars=context.bars[:2])
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    try:assert not capture.submit(s,context)
    finally:summary=capture.close()
    assert summary['counts']['invalid_inputs']==1 and summary['counts'].get('written',0)==0


def test_duplicates_limit_and_close_do_not_retry_or_block_orders(tmp_path,monkeypatch):
    monkeypatch.setattr('app.asset_modules.grok_observer_capture.MAX_RECORDS',1)
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    s=snap()
    try:
        assert capture.submit(s,evidence(s))
        assert not capture.submit(s,evidence(s))
        other=snap(event_id='different')
        assert not capture.submit(other,evidence(other))
    finally:summary=capture.close()
    assert summary['counts']['duplicates']==summary['counts']['limit_rejections']==1
    assert not capture.submit(other,evidence(other))


def test_queue_backpressure_drops_without_wait_or_exception(tmp_path,monkeypatch):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    def full(*args):raise Full
    monkeypatch.setattr(capture._queue,'put_nowait',full)
    try:assert not capture.submit(snap(),evidence())
    finally:summary=capture.close()
    assert summary['counts']['queue_full']==1 and summary['counts'].get('written',0)==0


def test_storage_failure_is_visible_without_engine_exception(tmp_path,monkeypatch):
    def fail(self):
        with self._lock:self._failed=True;self._counts['writer_failures']+=1
    monkeypatch.setattr(ContextCapture,'_write',fail)
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    capture._thread.join(timeout=1)
    assert not capture.submit(snap(),evidence())
    summary=capture.close()
    assert summary['writer_failed'] and summary['writer_stopped']


def test_entire_input_validates_before_first_provider_call(tmp_path):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    try:assert capture.submit(snap(),evidence())
    finally:capture.close()
    path=tmp_path/'capture'/'private-observer-context.jsonl'
    with path.open('a') as stream:stream.write('{"bad":"PRIVATE_TEXT"}\n')
    trial,requests,_=fixture()
    with pytest.raises(ValueError):observe_capture(path,trial,BASE)
    assert not requests


def test_saved_context_is_never_retimed_for_model_request(tmp_path):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    try:assert capture.submit(snap(),evidence())
    finally:capture.close()
    trial,requests,clock=fixture();clock[0]=NOW+timedelta(seconds=3)
    result=observe_capture(tmp_path/'capture'/'private-observer-context.jsonl',trial,BASE)
    assert result[0].status=='STALE_OR_FUTURE_QUOTE' and not requests
