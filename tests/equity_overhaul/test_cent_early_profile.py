from dataclasses import replace
from datetime import UTC,datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS
import json
import pytest
from app.configuration import load_configuration
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,ENV,V2_FAMILIES
from app.asset_modules.paper_day_setup import prepare
from app.asset_modules.equity_policy_attribution import family
from app.strategies.warrior_momentum.configuration import SetupConfig,WarriorMomentumConfig
from app.strategies.warrior_momentum.models import SetupType,SetupState
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig

NOW=datetime(2026,10,30,13,30,tzinfo=UTC)


def test_v1_spec_stays_readable_and_v2_is_explicit_and_immutable(tmp_path):
    create(tmp_path/'v1',lambda:NOW)
    _,v1=load(tmp_path/'v1/experiment.json',lambda:NOW)
    assert v1['scope']=='NATIVE_PAPER_EXPERIMENT_V1'
    create(tmp_path/'v2',lambda:NOW,profile='CENT_EARLY_V2')
    path,spec=load(tmp_path/'v2/experiment.json',lambda:NOW)
    assert spec['limits']==v1['limits'] and spec['trial_id']!=v1['trial_id']
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
    assert p.cent_early and p.status()['approved_policies']==[]
    c=p.warrior_config(WarriorMomentumConfig())
    assert c.setups.allowed_setup_types==frozenset(V2_FAMILIES['WARRIOR_MOMENTUM'])
    q=p.scalper_config(QuickScalperConfig(enabled=True))
    assert (q.cent_target_minimum,q.cent_target_maximum,q.minimum_net_target_reward_r)==(D('.05'),D('.10'),D(1))
    spec['cent_policy']['maximum']='1';path.write_text(json.dumps(spec))
    with pytest.raises(ValueError):load(path,lambda:NOW)


def test_v2_admission_uses_real_native_names_and_still_requires_fingerprints(tmp_path):
    create(tmp_path,lambda:NOW,profile='CENT_EARLY_V2');path,spec=load(tmp_path/'experiment.json',lambda:NOW)
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0);p.capture_ready=lambda:True
    p.bind(NS(_code={'WARRIOR_MOMENTUM_V1':'a'*64,'QUICK_SCALPER':'c'*64},
        _configuration={'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64}))
    m=dict(strategy_trial_id=spec['trial_id'],strategy_owner='WARRIOR_MOMENTUM',
        strategy_code_fingerprint='a'*64,strategy_configuration_fingerprint='b'*64)
    for name in V2_FAMILIES['WARRIOR_MOMENTUM']:assert p.permits(dict(m,strategy_family=name))
    for name in ['HIGH_OF_DAY_BREAKOUT','FIRST_PULLBACK','UNKNOWN']:
        assert not p.permits(dict(m,strategy_family=name))
    assert not p.permits(dict(m,strategy_family='MICRO_PULLBACK',strategy_code_fingerprint='x'))


def test_native_selection_happens_before_ranking_not_just_at_gateway(monkeypatch):
    from app.strategies.warrior_momentum import setups
    hod=NS(state=SetupState.TRIGGERED,score=D(99),setup_type=SetupType.HIGH_OF_DAY_BREAKOUT)
    early=NS(state=SetupState.TRIGGERED,score=D(70),setup_type=SetupType.MICRO_PULLBACK)
    absent=NS(state=SetupState.NOT_FORMED,score=D(0),setup_type=SetupType.BULL_FLAG)
    for name in ['detect_hod_breakout','detect_micro_pullback','detect_bull_flag','detect_flat_top',
                 'detect_momentum_acceleration','detect_momentum_reacceleration','detect_reclaim_continuation']:
        value=hod if name=='detect_hod_breakout' else early if name=='detect_micro_pullback' else absent
        monkeypatch.setattr(setups,name,lambda *args,v=value:v)
    assert setups.detect_best_setup(()) is hod
    assert setups.detect_best_setup((),SetupConfig(allowed_setup_types=frozenset({'MICRO_PULLBACK'}))) is early
    assert setups.detect_best_setup((),SetupConfig(allowed_setup_types=frozenset({'RECLAIM_CONTINUATION'}))) is None
    with pytest.raises(ValueError):SetupConfig(allowed_setup_types=frozenset({'FIRST_PULLBACK'}))


def test_v2_attribution_does_not_mislabel_native_setup_as_research_primary():
    signal=NS(setup_type=SetupType.MICRO_PULLBACK,taxonomy_strategy_id='FIRST_PULLBACK')
    assert family(signal,'WARRIOR_MOMENTUM_V1')=='FIRST_PULLBACK'
    assert family(signal,'WARRIOR_MOMENTUM_V1',native_only=True)=='MICRO_PULLBACK'


def test_real_micro_pullback_can_trigger_below_the_prior_high_of_day():
    from app.strategies.warrior_momentum.setups import detect_best_setup
    from tests.warrior_momentum.test_strategy import micro_bars
    bars=micro_bars()
    bars=(*bars[:-1],replace(bars[-1],high=D('10.58'),close=D('10.57')))
    selected=detect_best_setup(bars,SetupConfig(allowed_setup_types=frozenset(V2_FAMILIES['WARRIOR_MOMENTUM'])))
    assert selected.setup_type is SetupType.MICRO_PULLBACK and selected.state is SetupState.TRIGGERED
    assert selected.trigger < bars[-1].close < max(b.high for b in bars[:-1])


def test_preparation_is_new_profile_only_and_does_not_create_lease_or_launch(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();(repo/'.env').write_text('UNCHANGED=yes')
    report=prepare(repo,tmp_path/'trial',profile='CENT_EARLY_V2')
    text=(tmp_path/'trial/Start-Atlas-Paper-Trial.ps1').read_text()
    assert "--profile 'CENT_EARLY_V2'" in text
    assert report['experiment_profile']=='CENT_EARLY_V2'
    assert not (tmp_path/'trial/experiment.json').exists()
    assert (repo/'.env').read_text()=='UNCHANGED=yes'


def test_real_v2_desktop_binds_profiles_to_both_engines_and_retains_protection(monkeypatch,tmp_path,historical_intelligence_artifact):
    import app.composition.desktop as module
    create(tmp_path/'trial',profile='CENT_EARLY_V2');monkeypatch.setenv(ENV,str(tmp_path/'trial/experiment.json'))
    c=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','ATLAS_DAILY_RESEARCH_ENABLED':'true',
        'WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'capture.sqlite3'),'ALLOWED_SYMBOLS':'XYZ'})
    monkeypatch.setattr(module,'load_configuration',lambda:c)
    graph=module.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3')
    try:
        assert graph.equity_paper_experiment.active()
        assert graph.warrior_forward_sidecar.strategy_config.setups.allowed_setup_types==frozenset(V2_FAMILIES['WARRIOR_MOMENTUM'])
        assert graph.quick_scalper_runtime.config.cent_target_maximum==D('.10')
        assert graph.quick_scalper_runtime.risk_config.maximum_quantity==10
        assert graph.equity_paper_experiment.status()['protective_sells']=='AVAILABLE'
    finally:graph.close(timeout_seconds=1)
