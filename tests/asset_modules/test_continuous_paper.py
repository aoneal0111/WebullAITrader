"""Offline integration of persistent paper operation, transport accounting and coverage."""
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import Mock
import pytest

from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,ACCOUNT_SIZING_PROFILE
from app.asset_modules.grok_engine_teams import TeamBudget
from app.asset_modules.grok import GrokClient,GrokUnavailable
from app.asset_modules.grok_warrior_control import WarriorControl
from app.asset_modules.paper_morning_status import report
from app.asset_modules.daily_research_journal import DailyJournal
from tests.asset_modules.test_controlled_paper_service import setup,NOW
from tests.asset_modules.test_grok_runtime_handoff import warrior
from tests.asset_modules.test_grok_warrior_control import control,point


def operation(tmp_path,clock=lambda:NOW):
    root=tmp_path/'operation'
    create(root,clock=clock,profile=ACCOUNT_SIZING_PROFILE,continuous=True,extended_sessions=True,clock_tolerance=True)
    path,spec=load(root/'experiment.json',clock=clock)
    exp=PaperExperiment(path,spec,clock=clock,timer=lambda:0);exp.capture_ready=lambda:True
    return exp


def test_no_expiry_after_month_and_no_strategy_parameter_change(tmp_path):
    clock=[NOW];exp=operation(tmp_path,lambda:clock[0])
    bounded=tmp_path/'bounded';create(bounded,clock=lambda:NOW,profile=ACCOUNT_SIZING_PROFILE,extended_sessions=True,clock_tolerance=True)
    p,spec=load(bounded/'experiment.json',clock=lambda:NOW)
    prior=PaperExperiment(p,spec,clock=lambda:NOW)
    from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
    from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
    assert exp.warrior_config(WarriorMomentumConfig())==prior.warrior_config(WarriorMomentumConfig())
    assert exp.scalper_config(QuickScalperConfig())==prior.scalper_config(QuickScalperConfig())
    clock[0]+=timedelta(days=31)
    assert exp.active() and load(exp.path,clock=lambda:clock[0])[1]['expires_at'] is None
    assert 'CONTINUOUS PAPER' in exp.notice()
    exp.path.with_name('PAUSE_ENTRIES').touch();assert not exp.active()


def test_continuous_cannot_be_enabled_by_removing_legacy_expiry(tmp_path):
    create(tmp_path,clock=lambda:NOW,profile=ACCOUNT_SIZING_PROFILE)
    path=tmp_path/'experiment.json';spec=json.loads(path.read_text());spec['expires_at']=None
    path.write_text(json.dumps(spec))
    with pytest.raises((ValueError,TypeError)):load(path,clock=lambda:NOW)


def test_durable_warrior_budget_beyond_all_legacy_caps(tmp_path):
    exp=operation(tmp_path);budget=TeamBudget(exp.path.parent,clock=lambda:NOW)
    for index in range(130):
        identifier=budget.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':str(index)})
        budget.finish(identifier,{'status':'RESPONSE_RECEIVED'})
    row=budget.report();assert row['reserved_attempts']==130 and row['maximum_attempts'] is None
    with pytest.raises(GrokUnavailable,match='DUPLICATE_TEAM_EVENT'):
        budget.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'0'})
    exp.path.with_name('STOP_GROK').touch()
    with pytest.raises(GrokUnavailable):budget.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'new'})


def persistent_control(control,tmp_path):
    exp=operation(tmp_path)
    c=WarriorControl(control.sidecar,control.bridge,control.service,exp,control.ownership,control.client,clock=control.clock)
    c.client.request.return_value=dict(action='PASS',symbol='SPY',entry='0',stop='0',target='0',quantity=0,reason='Fixture')
    return c


def test_full_watchlist_rotates_and_no_call_while_result_pending(control,tmp_path,setup):
    c=persistent_control(control,tmp_path);clock=setup[-1]
    try:
        for index in range(12):
            symbol=f'S{index:02}'
            p=point();p=replace(p,observation=replace(p.observation,symbol=symbol))
            c._contexts[symbol]=(p,clock[0])
        for batch in range(3):
            now=NOW+timedelta(seconds=10*batch);clock[0]=now
            c._contexts={s:(p,now) for s,(p,_) in c._contexts.items()}
            c._review_director()
            before=c.client.request.call_count;c._review_director()
            assert c.client.request.call_count==before
            c._completed.clear()
        assert len(c._coverage)==12
        assert c.client.maximum_requests is None and c.client.role_cooldown_seconds==0
        assert c.budget.report()['reserved_attempts']==3
        assert c.coverage_status()['fresh_not_yet_presented']==[]
    finally:c.stop();c.producer.request_stop()


def test_no_claim_of_grok_coverage_when_transport_fails(control,tmp_path):
    c=persistent_control(control,tmp_path)
    c._contexts['SPY']=(point(),NOW)
    c.client.request.side_effect=GrokUnavailable('GROK_BUSY')
    try:
        with pytest.raises(GrokUnavailable):c._review_director()
        assert not c._coverage
    finally:c.stop();c.producer.request_stop()


def test_status_does_not_report_expired_or_twenty_call_quota(tmp_path):
    exp=operation(tmp_path)
    status=report(exp.path.parent)
    assert status['entry_window_state']=='CONTINUOUS'
    assert status['remaining_trial_hours'] is None and status['entry_blockers']==[]
    assert status['position_sizing']=='NATIVE_ACCOUNT_RISK'


def test_capture_continues_past_old_caps_without_deleting_records(tmp_path):
    journal=DailyJournal(tmp_path/'journal','a'*64,max_bytes=1,max_records=1,continuous=True)
    for i in range(5):assert journal.submit('FIXTURE',{'i':i})
    status=journal.close()
    assert status['counts']['written']==5 and status['evidence_complete']
    assert not status['capacity_reached'] and journal.max_records is None


def test_preparation_is_manual_no_expiry_or_old_quota_notice(tmp_path,monkeypatch):
    from app.asset_modules.paper_day_setup import prepare
    monkeypatch.setenv('XAI_API_KEY','fixture-key');monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')
    repo=tmp_path/'repo';repo.mkdir()
    result=prepare(repo,tmp_path/'launchers',profile=ACCOUNT_SIZING_PROFILE,continuous=True,extended_sessions=True,clock_tolerance=True)
    script=(tmp_path/'launchers/Start-Atlas-Paper.ps1').read_text()
    assert '--continuous' in script and 'MAX 20 PAID' not in script and '8 HOURS' not in script
    assert not (tmp_path/'launchers/experiment.json').exists()
    assert result['shared_warrior_paid_cap'] is None and result['entry_window_hours'] is None
    assert 'fixture-key' not in script


def test_client_crosses_120_calls_only_when_explicitly_configured():
    from tests.asset_modules.test_grok import response
    client=GrokClient('fixture-key','grok-test',transport=lambda _:response({}),clock=lambda:0)
    client.maximum_requests=None;client.role_cooldown_seconds=0
    for _ in range(130):assert client.request('MASTER','',{'mode':'PAPER'},{})=={}
    assert client.requests==130
    with pytest.raises(ValueError):client.request('MASTER','',{'mode':'LIVE'},{})


def test_persistent_capture_has_no_time_or_total_quota(tmp_path,monkeypatch):
    from app.configuration import load_configuration
    from app.asset_modules.daily_research import DailyResearch
    exp=operation(tmp_path)
    monkeypatch.setenv('ATLAS_EQUITY_PAPER_EXPERIMENT',str(exp.path))
    configuration=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','ATLAS_DAILY_RESEARCH_ENABLED':'true'})
    recorder=DailyResearch(configuration,root=tmp_path,clock=lambda:NOW)
    try:
        assert recorder._deadline is None
        assert recorder.journal.max_bytes is None and recorder.journal.max_records is None
        manifest=json.loads((recorder.directory/'research-manifest.json').read_text())
        assert manifest['max_seconds'] is None and manifest['live_trading_enabled'] is False
    finally:recorder.close()


def test_operation_claims_more_than_fifty_and_retains_source_binding(tmp_path):
    from types import SimpleNamespace as NS
    from tests.equity_overhaul.test_account_paper_sizing import placement
    exp=operation(tmp_path)
    exp.bind(NS(_code={'WARRIOR_MOMENTUM_V1':'a'*64,'QUICK_SCALPER':'c'*64},
        _configuration={'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64}))
    for i in range(55):assert exp.claim_order(placement(exp,str(i)))
    bad=placement(exp,'bad');bad.order.metadata['strategy_code_fingerprint']='e'*64
    assert not exp.claim_order(bad) and not exp.claim_order(placement(exp,'0'))


def test_continuous_director_entry_fill_and_protection_use_canonical_path(control,tmp_path,setup,warrior):
    from tests.asset_modules.test_grok_warrior_control import apply
    from tests.asset_modules.test_controlled_paper_service import fill
    c=persistent_control(control,tmp_path)
    try:
        c._contexts['SPY']=(point(),NOW)
        apply(c,warrior)
        fill(setup[0])
        assert warrior.reconcile_authoritative_protection('SPY',NOW)
        c.experiment.path.with_name('STOP_GROK').touch()
        assert not c.active()
        assert warrior.reconcile_authoritative_protection('SPY',NOW)
    finally:c.stop();c.producer.request_stop()


def test_operation_accepts_more_than_old_sixty_four_symbol_cache(control,tmp_path,warrior):
    c=persistent_control(control,tmp_path)
    c._thread=Mock()  # Exercise real offers without starting a network worker.
    try:
        for i in range(70):
            symbol=f'S{i:02}'
            original=point()
            p=replace(original,observation=replace(original.observation,symbol=symbol),
                bars=tuple(replace(bar,symbol=symbol) for bar in original.bars))
            c.offer(p,warrior)
        assert len(c._contexts)==70
        assert c.coverage_status()['fresh_symbols']==70
    finally:c.stop();c.producer.request_stop()


def test_full_library_reaches_director_with_explicit_unavailable_entries(control,tmp_path):
    from app.equity_overhaul.warrior_library_repair import migrate
    c=persistent_control(control,tmp_path)
    try:
        migrate(c.root)
        path,spec=load(c.experiment.path,clock=c.clock)
        c.experiment=PaperExperiment(path,spec,clock=c.clock,timer=lambda:0)
        c.experiment.capture_ready=lambda:True
        c._contexts['SPY']=(point(),NOW)
        c._review_director()
        evidence=c.client.request.call_args.args[2]
        catalog=evidence['warrior_strategy_library']
        assert len(catalog)==30
        assert sum(row['availability']=='ACTIVE' for row in catalog)==23
        assert all(row['unavailable_reason'] for row in catalog if row['availability']!='ACTIVE')
        status=report(c.root)
        assert status['warrior_library_policy']=='IMPLEMENTED_TAXONOMY_PAPER_V1'
        assert len(status['warrior_detector_catalog'])==30
    finally:c.stop();c.producer.request_stop()
