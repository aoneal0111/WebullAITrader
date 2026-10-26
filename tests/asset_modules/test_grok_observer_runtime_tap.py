from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_observer_capture import ContextCapture,load_capture
from app.asset_modules.grok_observer_runtime_tap import RuntimeContextTap
from app.strategies.warrior_momentum.execution_quote import ExecutionQuoteSnapshot,WebullExecutionQuoteSource
from app.strategies.warrior_momentum.models import MinuteBar
from tests.asset_modules.test_grok_observer import NOW,BASE


class FixtureSource(WebullExecutionQuoteSource):
    def __init__(self):self.calls=0
    def __call__(self,*args):self.calls+=1;raise AssertionError('Tap must not request data')


def bars(symbol='AAPL'):
    return tuple(MinuteBar(symbol,NOW-timedelta(minutes=6-i),D(100),D(101),D(99),D(100),D(1000)) for i in range(5))


def quote():
    at=NOW-timedelta(seconds=.2)
    return ExecutionQuoteSnapshot('AAPL',D(100),D(100),D('100.1'),at,at,at,NOW)


def test_native_queue_preserves_two_owners_and_only_shares_market_bars(tmp_path):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    tap=RuntimeContextTap(capture,BASE);source=FixtureSource()
    try:
        assert tap.remember_bars('AAPL',bars(),known_at=NOW)
        for engine,result in ((EngineId.WARRIOR,'BLOCKED'),(EngineId.SCALPER,'WAIT')):
            assert tap.record(engine=engine,quote=quote(),source=source,at=NOW,
                baseline_result=result,setup_state='FORMING')
    finally:report=tap.close()
    assert report['tap_stopped'] and report['capture']['writer_stopped']
    assert report['tap_counts']['captured']==2 and source.calls==0
    assert report['tap_counts']['bars_queued']==1 and report['tap_counts']['assessments_queued']==2
    assert report['tap_counts']['assessments_queued_WARRIOR_MOMENTUM_V1']==1
    assert report['tap_counts']['assessments_queued_QUICK_SCALPER']==1
    records=load_capture(tmp_path/'capture'/'private-observer-context.jsonl',BASE)
    assert records[0][1].baseline_result=='BLOCKED' and records[1][1].baseline_result=='WAIT'
    assert records[0][0].engine is EngineId.WARRIOR and records[1][0].engine is EngineId.SCALPER


@pytest.mark.parametrize('mode',['no_bars','foreign_bars','late_bars','custom_source','stale_quote'])
def test_missing_or_unproven_native_evidence_never_fabricates_context(tmp_path,mode):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    tap=RuntimeContextTap(capture,BASE)
    try:
        if mode!='no_bars':tap.remember_bars('AAPL',bars('MSFT' if mode=='foreign_bars' else 'AAPL'),
            known_at=NOW+timedelta(seconds=1) if mode=='late_bars' else NOW)
        q=quote()
        if mode=='stale_quote':q=replace(q,bid_timestamp=NOW-timedelta(seconds=4),ask_timestamp=NOW-timedelta(seconds=4))
        tap.record(engine=EngineId.SCALPER,quote=q,source=object() if mode=='custom_source' else FixtureSource(),
            at=NOW,baseline_result='WAIT',setup_state='FORMING')
    finally:report=tap.close()
    assert report['tap_counts'].get('captured',0)==0
    assert report['capture']['counts'].get('written',0)==0


class SpyTap(RuntimeContextTap):
    def __init__(self,fail=False):self.frames=[];self.assessments=[];self.fail=fail
    def remember_bars(self,*args,**kwargs):
        self.frames.append((args,kwargs))
        if self.fail:raise RuntimeError('Local observer failure')
    def record(self,**kwargs):
        self.assessments.append(kwargs)
        if self.fail:raise RuntimeError('Local observer failure')


@pytest.mark.parametrize('fail',[False,True])
def test_warrior_native_hook_does_not_change_signal_or_provider_request_count(tmp_path,fail):
    from tests.warrior_momentum.test_execution_quote_confirmation import snapshot,evaluate,NOW as AT
    from tests.warrior_momentum.test_forward_capture import point,account
    from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
    from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
    from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
    baseline=evaluate(tmp_path/'baseline',lambda _:snapshot())
    calls=[];tap=SpyTap(fail)
    store=ForwardCaptureStore(tmp_path/'native.sqlite3');writer=ForwardCaptureWriter(store,flush_interval_seconds=.01)
    service=WarriorForwardCaptureService(store,writer,execution_quote_source=lambda s:calls.append(s) or snapshot(),observer_context_tap=tap)
    try:result=service.observe(point(evaluation_timestamp=AT,quote_freshness_seconds=D(30),last_price_freshness_seconds=D(30)),account=account())
    finally:writer.close()
    assert result[0].status==baseline[0].status and result[1]==baseline[1]
    assert calls==['XYZ'] and len(tap.frames)==len(tap.assessments)==1
    assert tap.assessments[0]['engine'] is EngineId.WARRIOR


@pytest.mark.parametrize('fail',[False,True])
def test_scalper_native_hook_does_not_change_submission_or_provider_calls(fail):
    from tests.warrior_momentum.test_quick_scalper_runtime import Bridge,Quotes,quote as q,snapshot,account,NOW as AT
    from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
    from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
    from app.paper_trading.order_book import PaperOrderBook
    results=[]
    for tap in (None,SpyTap(fail)):
        bridge=Bridge();source=Quotes(q())
        runtime=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=bridge,
            order_book=PaperOrderBook(),account_context_source=account,position_quantity_source=lambda _:D(0),
            execution_quote_source=source,clock=lambda:AT,observer_context_tap=tap)
        value=snapshot();runtime._snapshots[('FAST','g1')]=value
        result=runtime.scalper.observe(value)
        results.append((result.decision,len(bridge.entries),source.calls))
        if tap is not None:
            assert len(tap.assessments)==1 and tap.assessments[0]['engine'] is EngineId.SCALPER
    assert results[0]==results[1] and results[0][2]==1


@pytest.mark.usefixtures('historical_intelligence_artifact')
def test_composition_passes_only_explicit_tap_to_both_engines(tmp_path,monkeypatch):
    from app.composition import desktop
    from app.configuration import load_configuration
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'forward.sqlite3')})
    monkeypatch.setattr(desktop,'load_configuration',lambda:config)
    tap=SpyTap()
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'execution.sqlite3',observer_context_tap=tap)
    try:
        assert composition.quick_scalper_runtime._observer_context_tap is tap
        assert composition.warrior_forward_sidecar._observer_context_tap is tap
        assert composition.runtime_service.status.value=='STOPPED'
    finally:composition.close(timeout_seconds=1)


@pytest.mark.parametrize('flag',['live_trading_enabled','grok_paper_entries_enabled'])
def test_composition_rejects_unsafe_capture_before_provider_construction(monkeypatch,flag):
    from types import SimpleNamespace
    from app.composition import desktop
    config=SimpleNamespace(environment=SimpleNamespace(value='PAPER'),
        live_trading_enabled=flag=='live_trading_enabled',grok_paper_entries_enabled=flag=='grok_paper_entries_enabled')
    monkeypatch.setattr(desktop,'load_configuration',lambda:config)
    with pytest.raises(ValueError,match='safe PAPER flags'):
        desktop.create_desktop_composition(observer_context_tap=SpyTap())



def test_bar_only_capture_is_explicitly_not_an_engine_assessment(tmp_path):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW)
    tap=RuntimeContextTap(capture,BASE)
    try:
        for _ in range(3):assert tap.remember_bars('AAPL',bars(),known_at=NOW)
    finally:report=tap.close()
    assert report['tap_counts']['queued']==report['tap_counts']['bars_queued']==3
    assert report['tap_counts']['assessments_queued']==0
    assert report['capture']['records_by_engine']=={}



def test_post_request_quote_uses_confirmation_time_and_keeps_native_event_order(tmp_path):
    now=NOW+timedelta(seconds=.1)
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:now)
    tap=RuntimeContextTap(capture,BASE)
    q=replace(quote(),confirmed_at=NOW-timedelta(seconds=.1))
    try:
        tap.remember_bars('AAPL',bars(),known_at=NOW-timedelta(seconds=.4))
        tap.record(engine=EngineId.WARRIOR,quote=q,source=FixtureSource(),at=NOW-timedelta(seconds=.3),
            baseline_result='BLOCKED',setup_state='FORMING')
    finally:report=tap.close()
    assert report['tap_counts']['captured']==1
    assert report['tap_counts']['quote_after_native_event']==1
    assert report['tap_counts'].get('future_quote_at_assessment',0)==0
    assert report['timing']['quote_age_at_native_event_seconds']['min']==-.1
    assert report['timing']['quote_age_at_confirmation_seconds']['min']==.1
    assert report['timing']['observer_queue_delay_seconds']['min']==0
    records=load_capture(tmp_path/'capture'/'private-observer-context.jsonl',BASE)
    assert records[0][0].captured_at==q.confirmed_at
    assert records[0][0].bid_at==NOW-timedelta(seconds=.2)  # Original provider time is preserved.


def test_truly_future_or_stale_provider_quote_remains_refused(tmp_path):
    for label,provider_time in [('old',NOW-timedelta(seconds=45)),('future',NOW+timedelta(seconds=1))]:
        capture=ContextCapture(tmp_path/label,BASE,clock=lambda:NOW)
        tap=RuntimeContextTap(capture,BASE)
        q=replace(quote(),bid_timestamp=provider_time,ask_timestamp=provider_time,confirmed_at=NOW)
        try:
            tap.remember_bars('AAPL',bars(),known_at=NOW)
            tap.record(engine=EngineId.WARRIOR,quote=q,source=FixtureSource(),at=NOW,
                baseline_result='BLOCKED',setup_state='FORMING')
        finally:report=tap.close()
        assert report['tap_counts']['capture_refused']==1
        assert report['capture']['records_by_engine']=={}
        assert report['timing']['quote_age_at_confirmation_seconds']['min']==(45 if label=='old' else -1)
