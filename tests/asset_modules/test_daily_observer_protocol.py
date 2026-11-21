from types import SimpleNamespace
from datetime import timedelta
from time import monotonic,sleep
import json

import pytest
from app.asset_modules.daily_research_journal import RecordingMarketObserver,DailyJournal
from tests.asset_modules.test_grok_observer import BASE


def test_transparent_protocol_preserves_all_dynamic_methods_and_absent_attributes():
    calls=[]
    names=('start','stop','bind_scanner_adapter','bind_scanner_decision_source',
        'bind_premarket_volume_profile','observe_scanner_decision','session_policy_tick',
        'overnight_capability_lost','retained_symbols_nonblocking','needs_historical_preload',
        'preload_historical_bars','fail_closed_market_data','projection_metrics')
    native=SimpleNamespace(**{name:(lambda *a,_name=name,**kw:calls.append((_name,a,kw))) for name in names})
    wrapped=RecordingMarketObserver(native,SimpleNamespace())
    for name in names:
        method=getattr(wrapped,name)
        assert method is getattr(native,name)
        method('fixture')
    assert [c[0] for c in calls]==list(names)
    assert not hasattr(wrapped,'not_a_native_method')


def test_recording_failure_cannot_hide_native_result_or_exception():
    class BrokenJournal:
        def observe_market_event(self,event):raise OSError('fixture only')
    event=object();seen=[]
    wrapped=RecordingMarketObserver(lambda e:seen.append(e) or 'native-result',BrokenJournal())
    assert wrapped(event)=='native-result' and seen==[event]
    def failed(e):raise ValueError('native failure')
    with pytest.raises(ValueError,match='native failure'):RecordingMarketObserver(failed,BrokenJournal())(event)


def test_real_composite_starts_binds_and_feeds_scalper_through_recorder(tmp_path):
    from app.strategies.warrior_momentum.desktop_sidecar import CompositeMarketEventObserver,WarriorDesktopSidecar
    from tests.warrior_momentum.test_desktop_sidecar import adapter,quote,trade,T0,RecordingQuickScalper
    scanner=adapter();scalper=RecordingQuickScalper()
    sidecar=WarriorDesktopSidecar(enabled=True,storage_path=tmp_path/'forward.sqlite3',
        quick_scalper_observer=scalper,clock=lambda:T0)
    native=CompositeMarketEventObserver(None,sidecar)
    journal=DailyJournal(tmp_path/'journal',BASE,clock=lambda:T0)
    wrapped=RecordingMarketObserver(native,journal)
    wrapped.bind_scanner_adapter(scanner);wrapped.start('PAPER')
    try:
        assert sidecar._adapter is scanner and sidecar._service is not None
        for event in (quote(T0),trade(2,T0+timedelta(seconds=1),'10.20'),quote(T0+timedelta(seconds=2))):
            scanner.consume(event);wrapped(event)
        assert len(scalper.calls)==2
        assert scalper.calls[-1][0]=='XYZ'
    finally:
        wrapped.stop();report=journal.close()
    assert report['counts']['MARKET_EVENT']==3
    assert sidecar._service is None


@pytest.mark.usefixtures('historical_intelligence_artifact')
def test_normal_desktop_broker_boundary_retains_start_and_binding(tmp_path,monkeypatch):
    import app.composition.desktop as desktop
    import app.asset_modules.daily_research as recording
    from app.configuration import load_configuration
    from tests.warrior_momentum.test_desktop_sidecar import adapter,T0,quote,trade
    configuration=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'ATLAS_DAILY_RESEARCH_ENABLED':'true','WARRIOR_FORWARD_PAPER_ENABLED':'true',
        'QUICK_SCALPER_ENABLED':'true','WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'forward.sqlite3')})
    monkeypatch.setattr(desktop,'load_configuration',lambda:configuration)
    original=recording.DailyResearch
    monkeypatch.setattr(recording,'DailyResearch',lambda c:original(c,root=tmp_path,clock=lambda:T0))
    factory=desktop.create_desktop_runtime_service;captured={}
    def capture(*args,**kwargs):
        captured['observer']=kwargs['market_event_observer']
        return factory(*args,**kwargs)
    monkeypatch.setattr(desktop,'create_desktop_runtime_service',capture)
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3',paper_clock=lambda:T0)
    observer=captured['observer'];sidecar=composition.warrior_forward_sidecar
    scanner=adapter()
    try:
        assert isinstance(observer,RecordingMarketObserver)
        # Exactly the discovery protocol used by the normal broker driver.
        getattr(observer,'bind_scanner_adapter')(scanner)
        getattr(observer,'bind_scanner_decision_source')(lambda s:None,lambda s:True)
        getattr(observer,'start')('PAPER')
        assert sidecar._adapter is scanner
        assert sidecar._service is not None
        assert sidecar._service._observer_context_tap is composition.daily_research.tap
        for event in (quote(T0),trade(2,T0+timedelta(seconds=1),'10.20'),quote(T0+timedelta(seconds=2))):
            scanner.consume(event);observer(event)
        deadline=monotonic()+3
        while monotonic()<deadline:
            if composition.daily_research.journal.status()['counts'].get('BARS',0):break
            sleep(.01)
        # Proves real native engine data crosses the normal composition boundary;
        # the previous composition-only test never started or bound its observer.
        assert composition.daily_research.journal.status()['counts'].get('BARS',0)>0
    finally:
        observer.stop();composition.close(timeout_seconds=1)


def test_zero_engine_evidence_is_not_reported_as_ready(tmp_path):
    from app.asset_modules.daily_research import DailyResearch
    from app.configuration import load_configuration
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER'})
    recorder=DailyResearch(config,root=tmp_path)
    recorder.close()
    summary=json.loads((recorder.directory/'research-summary.json').read_text())
    assert summary['journal']['evidence_complete']
    assert summary['engine_evidence_status']=='NO_ENGINE_EVIDENCE_REVIEW_REQUIRED'
    assert summary['last_active_engine_readiness'] is None
    assert summary['entry_diagnostics']['scope']=='SINCE_OBSERVER_ATTACHMENT'
