from dataclasses import replace
from datetime import timedelta
from threading import Event
from time import monotonic
import json

import pytest
from app.configuration import load_configuration
from app.composition.runtime_mode import RuntimeMode
from app.asset_modules.daily_research import DailyResearch, configured_research
from app.asset_modules.daily_research_journal import DailyJournal, verify_journal, safe
from app.asset_modules.daily_research_compare import plan, review
from app.asset_modules.grok import GrokClient
from app.asset_modules.engine_catalog import EngineId
from tests.asset_modules.test_grok_observer import NOW, BASE
from tests.asset_modules.test_grok_observer_runtime_tap import bars, quote, FixtureSource


def config():
    return load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','ATLAS_DAILY_RESEARCH_ENABLED':'true'})


def test_default_disabled_and_no_unsafe_or_duplicate_recording(tmp_path):
    c=config()
    assert not load_configuration({}).daily_research_enabled
    assert configured_research(replace(c,daily_research_enabled=False),RuntimeMode.PAPER) is None
    with pytest.raises(ValueError):configured_research(c,RuntimeMode.SIMULATED)
    with pytest.raises(ValueError):configured_research(c,RuntimeMode.PAPER,object())
    with pytest.raises(ValueError):DailyResearch(replace(c,grok_paper_entries_enabled=True),root=tmp_path)
    with pytest.raises(ValueError):DailyResearch(replace(c,live_trading_enabled=True),root=tmp_path)


def test_queue_remains_nonblocking_while_writer_is_held(tmp_path,monkeypatch):
    entered=Event();release=Event();original=DailyJournal._write
    def held(self):
        entered.set();release.wait(5);original(self)
    monkeypatch.setattr(DailyJournal,'_write',held)
    journal=DailyJournal(tmp_path/'journal',BASE);assert entered.wait(1)
    before=monotonic()
    for i in range(3000):journal.submit('FIXTURE',{'number':i})
    assert monotonic()-before<1
    release.set();report=journal.close()
    assert report['counts']['queue_dropped']>0 and not report['evidence_complete']
    assert verify_journal(journal.directory)['evidence_complete'] is False


def test_chain_detects_modified_missing_or_partial_evidence(tmp_path):
    journal=DailyJournal(tmp_path/'journal',BASE,clock=lambda:NOW)
    journal.submit('FIXTURE',{'value':1});journal.close()
    assert verify_journal(journal.directory)['records']==1
    path=next(journal.directory.glob('events-*.jsonl'))
    original=path.read_bytes();path.write_bytes(original.replace(b'"value":1',b'"value":2'))
    with pytest.raises(ValueError):verify_journal(journal.directory)
    path.write_bytes(original[:-1])
    with pytest.raises(ValueError):verify_journal(journal.directory)
    path.unlink()
    with pytest.raises(ValueError):verify_journal(journal.directory)


def test_credentials_are_not_serialized():
    assert safe({'account_id':'PRIVATE','api_key':'PRIVATE','nested':{'password':'PRIVATE'},'price':1})=={'nested':{},'price':1}


def test_capacity_and_invalid_serialization_are_visible(tmp_path):
    journal=DailyJournal(tmp_path/'journal',BASE,max_records=1)
    journal.submit('FIXTURE',{'value':1});journal.submit('FIXTURE',{'value':2})
    report=journal.close()
    assert report['capacity_reached'] and not report['evidence_complete']
    assert verify_journal(journal.directory)['records']==1


def test_historical_same_evidence_end_to_end_and_no_retiming(tmp_path):
    recorder=DailyResearch(config(),root=tmp_path,clock=lambda:NOW)
    tap=recorder.tap
    tap.remember_bars('AAPL',bars(),known_at=NOW)
    for engine in (EngineId.WARRIOR,EngineId.SCALPER):
        tap.record(engine=engine,quote=quote(),source=FixtureSource(),at=NOW,
            baseline_result='BLOCKED',setup_state='FORMING')
    assert recorder.close()
    frozen=plan(recorder.directory)
    assert len(frozen['reviews'])==2
    assert frozen['journal']['evidence_complete']
    assert all(r['native_result']=='BLOCKED' for r in frozen['reviews'])
    calls=[]
    def transport(request):
        payload=json.loads(request.data);calls.append(payload)
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps({'view':'WATCH','reason':'Historical fixture only'}))])])).encode()
    client=GrokClient('fixture-only','grok-4.7',transport=transport)
    ticks=iter([0,21])
    result=review(frozen['reviews'][0],client,timer=lambda:next(ticks))
    assert result['status']=='HISTORICAL_REVIEW_ONLY'
    assert not result['execution_authorized'] and not result['would_fit_two_second_live_budget']
    sent=json.loads(calls[0]['input'][1]['content'])
    assert sent['captured_at']==NOW.isoformat() and sent['purpose']=='HISTORICAL_RESEARCH_ONLY'
    assert 'baseline_result' not in sent['strategy_evidence']
    assert 'tools' not in calls[0] and calls[0]['store'] is False
    capture=recorder.directory/'private-capture/private-observer-context.jsonl'
    capture.write_bytes(capture.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):plan(recorder.directory)


def test_stale_quote_is_retained_as_evidence_but_never_retimed_for_model(tmp_path):
    recorder=DailyResearch(config(),root=tmp_path,clock=lambda:NOW)
    recorder.tap.remember_bars('AAPL',bars(),known_at=NOW)
    stale=replace(quote(),bid_timestamp=NOW-timedelta(seconds=15),ask_timestamp=NOW-timedelta(seconds=15))
    recorder.tap.record(engine=EngineId.WARRIOR,quote=stale,source=FixtureSource(),at=NOW,
        baseline_result='BLOCKED',setup_state='FORMING')
    recorder.close()
    result=plan(recorder.directory)
    assert result['reviews']==[]
    rows=[json.loads(l) for p in (recorder.directory/'journal').glob('events-*.jsonl') for l in p.read_text().splitlines()]
    assert any(r['kind']=='ASSESSMENT' and r['payload']['quote']['bid_timestamp']==str(stale.bid_timestamp) for r in rows)


def test_normal_desktop_composes_recorder_without_starting_runtime(tmp_path,monkeypatch):
    import app.composition.desktop as desktop
    import app.asset_modules.daily_research as module
    original=module.DailyResearch
    monkeypatch.setattr(module,'DailyResearch',lambda c:original(c,root=tmp_path))
    monkeypatch.setattr(desktop,'load_configuration',config)
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3')
    recorder=composition.daily_research
    try:
        assert recorder is not None
        assert composition.quick_scalper_runtime._observer_context_tap is recorder.tap
        assert composition.warrior_forward_sidecar._observer_context_tap is recorder.tap
        assert composition.runtime_service.cycles_completed==0
        assert composition.paper_order_book.history()==()
    finally:composition.close(timeout_seconds=1)
    assert verify_journal(recorder.directory/'journal')['execution_authorized'] is False


def test_setup_only_enables_recording_and_preserves_private_lines(tmp_path,monkeypatch):
    from app.asset_modules.daily_research_setup import enable
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    monkeypatch.delenv('ATLAS_DAILY_RESEARCH_ENABLED',raising=False)
    monkeypatch.delenv('ATLAS_GROK_PAPER_ENTRIES_ENABLED',raising=False)
    repo=tmp_path/'repo';repo.mkdir()
    path=repo/'.env'
    original='WEBULL_TRADING_ENVIRONMENT=PAPER\nXAI_API_KEY=fixture-only\nQUICK_SCALPER_ENABLED=false\n'
    path.write_text(original)
    backup=enable(repo)
    assert backup.read_text()==original
    assert path.read_text()==original+'ATLAS_DAILY_RESEARCH_ENABLED=true\n'
    assert enable(repo) is None
    path.write_text(original+'ATLAS_GROK_PAPER_ENTRIES_ENABLED=true\n')
    before=path.read_bytes()
    with pytest.raises(ValueError):enable(repo)
    assert path.read_bytes()==before


def test_market_events_and_paper_fill_events_are_captured_without_submission(tmp_path):
    from decimal import Decimal as D
    from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
    from app.operations.runtime import PaperRuntimeEvent
    from app.paper_trading.models import PaperFill
    journal=DailyJournal(tmp_path/'journal',BASE,clock=lambda:NOW)
    event=MarketEvent(1,NOW,'AAPL','fixture',MarketEventType.QUOTE,QuotePayload(D(100),D('100.1'),D(20),D(20)))
    journal.observe_market_event(event)
    journal.observe_paper_event(PaperRuntimeEvent(1,NOW,'PAPER_FILL','Synthetic lifecycle fixture',0,symbol='AAPL',
        fill=PaperFill('fixture-order','AAPL','BUY',D(1),D(100),D(100),D(0),NOW)))
    report=journal.close()
    assert report['counts']['MARKET_EVENT']==report['counts']['PAPER_EVENT']==1
    assert verify_journal(journal.directory)['records']==2


def test_policy_changes_are_detected_without_reverting_user_configuration(tmp_path):
    from types import SimpleNamespace
    from dataclasses import dataclass
    @dataclass
    class Policy: enabled:bool=False
    recorder=DailyResearch(config(),root=tmp_path,clock=lambda:NOW)
    c=SimpleNamespace(quick_scalper_runtime=SimpleNamespace(config=Policy()),
        warrior_forward_sidecar=SimpleNamespace(strategy_config=Policy()))
    recorder.bind_baseline(c)
    c.quick_scalper_runtime.config=Policy(True)
    recorder.close()
    assert c.quick_scalper_runtime.config.enabled
    summary=json.loads((recorder.directory/'research-summary.json').read_text())
    assert not summary['policy_unchanged']
    with pytest.raises(ValueError,match='POLICY_CHANGED'):plan(recorder.directory)


def test_history_inventory_skips_source_clones_credentials_and_does_not_parse_performance(tmp_path):
    from app.asset_modules.research_history_inventory import inventory
    repo=tmp_path/'repo';research=repo/'data/research';research.mkdir(parents=True)
    (research/'full_research_final.json').write_text('{"private_performance":100}')
    private=tmp_path/'local/private-config-backups';private.mkdir(parents=True)
    (private/'summary.json').write_text('PRIVATE')
    result=inventory(repo,tmp_path/'local')
    assert len(result['receipts'])==1 and result['databases_opened']==0
    assert 'private_performance' not in json.dumps(result)


def test_comparison_cli_defaults_offline_and_two_online_roles_run_in_parallel(tmp_path,monkeypatch):
    import sys
    from threading import Barrier
    import app.asset_modules.daily_research_compare as compare
    recorder=DailyResearch(config(),root=tmp_path,clock=lambda:NOW)
    recorder.tap.remember_bars('AAPL',bars(),known_at=NOW)
    for engine in (EngineId.WARRIOR,EngineId.SCALPER):
        recorder.tap.record(engine=engine,quote=quote(),source=FixtureSource(),at=NOW,
            baseline_result='BLOCKED',setup_state='FORMING')
    recorder.close()
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'.env').write_text('WEBULL_TRADING_ENVIRONMENT=PAPER\nXAI_API_KEY=fixture-only\nATLAS_GROK_MODEL=grok-4.7\n')
    for key in ('XAI_API_KEY','ATLAS_GROK_MODEL','ATLAS_GROK_PAPER_ENTRIES_ENABLED'):
        monkeypatch.delenv(key,raising=False)
    calls=[];barrier=Barrier(2)
    def transport(request,**kwargs):
        payload=json.loads(request.data);calls.append(payload)
        assert payload['max_output_tokens']==256
        assert payload['reasoning']=={'effort':'low'}
        barrier.wait(3)
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps({'view':'PASS','reason':'Fixture only'}))])])).encode()
    import app.asset_modules.grok_research_transport as research_transport
    monkeypatch.setattr(research_transport,'_post',transport)
    base=['compare','--repo',str(repo),'--session',str(recorder.directory)]
    monkeypatch.setattr(sys,'argv',base+['--output',str(tmp_path/'offline')])
    compare.main()
    assert calls==[]
    monkeypatch.setattr(sys,'argv',base+['--output',str(tmp_path/'online'),'--online'])
    compare.main()
    result=json.loads((tmp_path/'online/comparison-summary.json').read_text())
    assert len(calls)==result['inference_attempts']==2
    assert all(r['status']=='HISTORICAL_REVIEW_ONLY' for r in result['reviews'])
    assert not result['native_path_waits_for_grok'] and result['orders']==0


def test_optional_storage_failure_does_not_block_normal_protection_start(monkeypatch):
    import app.asset_modules.daily_research as module
    def failed(*args,**kwargs):raise OSError('fixture storage failure')
    monkeypatch.setattr(module,'DailyResearch',failed)
    assert configured_research(config(),RuntimeMode.PAPER) is None
