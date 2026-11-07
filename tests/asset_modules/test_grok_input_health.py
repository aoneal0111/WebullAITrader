from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from app.asset_modules.daily_research_journal import canonical
from app.asset_modules.grok_input_health import JournalSymbols, input_health

NOW=datetime(2026,11,3,14,tzinfo=UTC)
BASE='a'*64


def line(kind='MARKET_ELIGIBILITY',payload=None,at=NOW,baseline=BASE):
    row=dict(schema='ATLAS_DAILY_EVIDENCE_V1',sequence=1,kind=kind,received_at=at.isoformat(),
        received_monotonic=1,baseline_sha256=baseline,previous_sha256='0'*64,
        payload=payload or {'symbol':'XYZ','tradable':True})
    row['sha256']=sha256(canonical(row)).hexdigest()
    return canonical(row)+b'\n'


def folder(tmp_path,data):
    root=tmp_path/'journal';root.mkdir()
    (root/'events-00001.jsonl').write_bytes(data)
    return root


def test_news_symbols_do_not_require_fresh_quotes_or_an_engine_case(tmp_path):
    folder(tmp_path,line('STREAM_ASSESSMENT',{'snapshot':{'symbol':'XYZ','bid_timestamp':'2020-01-01'}}))
    r=JournalSymbols(tmp_path,BASE)
    assert r.poll(NOW)==('XYZ',)
    assert r.poll(NOW)==()


def test_identity_freshness_and_equity_symbol_scope_are_checked(tmp_path):
    bad=line(payload={'symbol':'KEY;PLACE_ORDER'})
    old=line(payload={'symbol':'OLD'},at=NOW-timedelta(hours=1))
    crypto=line(payload={'symbol':'BTC/USD'})
    other=line(payload={'symbol':'OTHER'},baseline='b'*64)
    folder(tmp_path,bad+old+crypto+other+line(payload={'symbol':'GOOD'}))
    r=JournalSymbols(tmp_path,BASE)
    assert r.poll(NOW)==('GOOD',)
    assert r.counts['invalid_identity']==1 and r.counts['outside_recent_window']==1


def test_partial_rows_wait_for_complete_newline_and_rotation_reads_new_segment(tmp_path):
    data=line();root=folder(tmp_path,data[:-5])
    r=JournalSymbols(tmp_path,BASE)
    assert r.poll(NOW)==()
    with (root/'events-00001.jsonl').open('ab') as stream:stream.write(data[-5:])
    assert r.poll(NOW)==('XYZ',)
    (root/'events-00002.jsonl').write_bytes(line(payload={'symbol':'NEXT'}))
    assert r.poll(NOW)==('NEXT',)


def test_stale_quote_gate_and_missing_history_have_explicit_wait_reasons(tmp_path):
    (tmp_path/'engine-readiness.json').write_text(json.dumps({'tap_counts':{'stale_quote_at_capture':22,'captured':0}}))
    r=input_health(tmp_path,BASE,NOW)
    assert r['waiting_reason']=='NATIVE_QUOTES_STALE'
    assert r['latest_contexts']['QUICK_SCALPER']['status']=='NO_CONTEXT_FILE'
    assert r['orders']==0 and not r['execution_authorized']
    (tmp_path/'engine-readiness.json').write_text(json.dumps({'tap_counts':{'missing_shared_market_history':22}}))
    assert input_health(tmp_path,BASE,NOW)['waiting_reason']=='NATIVE_BAR_HISTORY_MISSING'


def test_live_team_runner_starts_news_without_fabricating_a_quote_case(tmp_path,monkeypatch):
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock
    import app.asset_modules.grok_day_proposals as day
    import app.asset_modules.grok_engine_teams as module
    import app.configuration.loader as loader
    import app.equity_overhaul.paper_experiment as experiment
    repo=tmp_path/'repo';repo.mkdir();trial=tmp_path/'trial'
    spec=experiment.create(trial,clock=lambda:NOW,profile='CENT_EXECUTABLE_V3')
    original_load=experiment.load
    monkeypatch.setattr(experiment,'load',lambda path,*a,**k:original_load(path,clock=lambda:NOW))
    session=tmp_path/'daily';session.mkdir();private=session/'private-capture';private.mkdir()
    (private/'private-observer-context.jsonl').write_bytes(b'')
    folder(session,line('STREAM_ASSESSMENT',{'snapshot':{'symbol':'XYZ','bid_timestamp':'2020-01-01'}}))
    (trial/'recording-session.json').write_text(json.dumps({'trial_id':spec['trial_id'],'session':str(session)}))
    source={'app/x.py':'a'*64}
    (session/'research-manifest.json').write_text(json.dumps(dict(source_sha256=sha256(canonical(source)).hexdigest(),
        configuration_sha256='b'*64,baseline_sha256=BASE,paper_experiment=spec)))
    monkeypatch.setattr(day,'source_manifest',lambda _:source)
    monkeypatch.setattr(day,'configuration_digest',lambda _:'b'*64)
    monkeypatch.setattr(loader,'load_configuration',lambda _:SimpleNamespace(environment=SimpleNamespace(value='PAPER'),
        live_trading_enabled=False,grok_paper_entries_enabled=False))
    monkeypatch.setattr(day,'configured_client',lambda *a,**k:Mock())
    made=[]
    def factory(*a,**k):
        c=Mock();c.usage.return_value={};c.diagnostics.return_value={}
        c.request.return_value={'watchlist':[],'reason':'Fixture only'};made.append(c);return c
    original_teams=module.EngineTeams
    monkeypatch.setattr(module,'EngineTeams',lambda env,path,clock:original_teams(
        {'XAI_API_KEY':'fixture-only','ATLAS_GROK_MODEL':'grok-4.7'},path,clock=clock,factory=factory))
    monkeypatch.setattr(module,'search_news',lambda *a:dict(status='CITED_DIGEST',digest='Fixture cited digest',
        citations=['https://example.com/announcement']))
    ticks=[0]
    def pause(seconds):ticks[0]+=seconds;time.sleep(.03)
    day.run(repo,trial,max_seconds=3,clock=lambda:NOW,timer=lambda:ticks[0],pause=pause,teams=True)
    summary=json.loads(next((trial/'grok-team-observer').glob('run-*/summary.json')).read_text())
    assert summary['cases_scheduled']==summary['records']==summary['orders']==0
    assert summary['shared_budget']['reserved_attempts']==2 and summary['accounting_final']
    assert made[0].request.call_count==made[1].request.call_count==0
    assert made[2].request.call_count==1
    health=json.loads(next((trial/'grok-team-observer').glob('run-*/input-health.json')).read_text())
    assert health['observer_counters']['observed_equity_rows']==1
    assert health['waiting_reason']=='NO_FRESH_NATIVE_CONTEXT_AVAILABLE'


def test_partial_native_checkpoint_does_not_stop_observation(tmp_path):
    (tmp_path/'engine-readiness.json').write_text('{"tap_counts":')
    result=input_health(tmp_path,BASE,NOW)
    assert result['native_checkpoint']=='UPDATING_OR_UNAVAILABLE'
    assert result['orders']==0
