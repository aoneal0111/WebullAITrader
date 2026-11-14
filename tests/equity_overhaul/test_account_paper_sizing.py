from dataclasses import replace
from datetime import UTC,datetime
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sqlite3
from contextlib import closing
import pytest
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,LIMITS,ACCOUNT_LIMITS
from app.asset_modules.paper_profiles import ACCOUNT_SIZING_PROFILE
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig

NOW=datetime(2026,11,5,14,tzinfo=UTC)


def experiment(root,profile):
    create(root,lambda:NOW,profile=profile,extended_sessions=True)
    path,spec=load(root/'experiment.json',lambda:NOW)
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0);p.capture_ready=lambda:True
    p.bind(NS(_code={'WARRIOR_MOMENTUM_V1':'a'*64,'QUICK_SCALPER':'c'*64},
        _configuration={'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64}))
    return p


def placement(p,request):
    metadata=dict(strategy_trial_id=p.spec['trial_id'],strategy_owner='WARRIOR_MOMENTUM',
        strategy_family='GROK_DIRECTED',strategy_code_fingerprint='a'*64,
        strategy_configuration_fingerprint='b'*64,source='autonomous-paper',reason='ENTRY',
        structural_stop='59.9',risk_dollars='10')
    return NS(order=NS(request_id=request,symbol='SPY',order_type=NS(value='LIMIT'),
        quantity=D(100),limit_price=D(60),metadata=metadata))


def test_new_account_profile_has_no_small_trial_caps_but_retains_normal_configuration(tmp_path):
    p=experiment(tmp_path,ACCOUNT_SIZING_PROFILE)
    assert p.account_sizing and p.spec['scope']=='NATIVE_PAPER_EXPERIMENT_V6'
    assert p.spec['limits']==ACCOUNT_LIMITS
    assert all(v is None for k,v in ACCOUNT_LIMITS.items() if k!='hours')
    normal=WarriorMomentumConfig()
    assert p.warrior_config(normal).risk==normal.risk
    assert 'AFTER_HOURS' in p.warrior_config(normal).entry.allowed_sessions
    assert p.status()['limits']==ACCOUNT_LIMITS
    assert 'Max 10 shares' not in p.notice()


def test_more_than_fifty_account_sized_claims_are_recorded_and_duplicates_refused(tmp_path):
    p=experiment(tmp_path,ACCOUNT_SIZING_PROFILE)
    for i in range(51):assert p.claim_order(placement(p,str(i)))
    assert not p.claim_order(placement(p,'0'))
    with closing(sqlite3.connect(p.db_path)) as db:
        assert db.execute('SELECT count(*) FROM attempts').fetchone()[0]==51
    p.path.with_name('PAUSE_ENTRIES').touch()
    assert not p.claim_order(placement(p,'52'))


def test_legacy_profile_caps_and_spec_identity_are_not_silently_upgraded(tmp_path):
    p=experiment(tmp_path,'GROK_WARRIOR_V5')
    assert p.spec['limits']==LIMITS
    assert not p.claim_order(placement(p,'large'))
    p.spec['limits']=ACCOUNT_LIMITS;p.path.write_text(json.dumps(p.spec))
    with pytest.raises(ValueError,match='SPEC_INVALID'):load(p.path,lambda:NOW)


def test_account_trial_claim_still_requires_exact_source_and_non_understated_risk(tmp_path):
    p=experiment(tmp_path,ACCOUNT_SIZING_PROFILE)
    bad=placement(p,'bad');bad.order.metadata['risk_dollars']='1'
    assert not p.claim_order(bad)
    bad=placement(p,'bad-source');bad.order.metadata['strategy_code_fingerprint']='e'*64
    assert not p.claim_order(bad)


def test_account_launcher_has_no_obsolete_size_or_entry_budget_notice(tmp_path,monkeypatch):
    from app.asset_modules.paper_day_setup import prepare
    monkeypatch.setenv('XAI_API_KEY','fixture-key');monkeypatch.setenv('ATLAS_GROK_MODEL','grok-test')
    repo=tmp_path/'repo';repo.mkdir();out=tmp_path/'trial'
    result=prepare(repo,out,profile=ACCOUNT_SIZING_PROFILE,extended_sessions=True)
    text=(out/'Start-Atlas-Paper-Trial.ps1').read_text()
    assert 'NATIVE ACCOUNT SIZING' in text and '10 SHARES' not in text and '$100 POSITION' not in text
    assert '--extended-sessions' in text and ACCOUNT_SIZING_PROFILE in text
    assert result['trial_size_overrides'] is False and result['trial_entry_attempt_cap'] is None
    assert result['shared_warrior_paid_cap']==20
    assert not (out/'experiment.json').exists()


def test_account_status_reports_no_trial_attempt_ceiling(tmp_path):
    from app.asset_modules.paper_morning_status import report
    p=experiment(tmp_path,ACCOUNT_SIZING_PROFILE)
    assert p.claim_order(placement(p,'one'))
    r=report(tmp_path)
    assert r['native_entry_attempts_used']==1 and r['native_entry_attempts_remaining'] is None
    assert r['position_sizing']=='NATIVE_ACCOUNT_RISK'
    assert r['trial_limits']['maximum_quantity'] is None


def test_model_cannot_change_a_v6_spec_into_a_new_limits_schedule(tmp_path):
    p=experiment(tmp_path,ACCOUNT_SIZING_PROFILE)
    p.spec['limits']['maximum_position_dollars']='999999'
    p.path.write_text(json.dumps(p.spec))
    with pytest.raises(ValueError,match='SPEC_INVALID'):load(p.path,lambda:NOW)
    assert ACCOUNT_LIMITS['maximum_position_dollars'] is None
