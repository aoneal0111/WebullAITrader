from dataclasses import asdict,replace
from datetime import timedelta
from decimal import Decimal as D
import json
from types import SimpleNamespace as NS
import pytest

from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,CLOCK_POLICY
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig,QuickScalperPolicy
from tests.warrior_momentum.test_cent_executable_v3 import config
from tests.warrior_momentum.test_quick_scalper import snapshot,NOW


def tolerant():return replace(config(),provider_future_tolerance_seconds=D('.25'))


@pytest.mark.parametrize('component',['last_timestamp','bid_timestamp','ask_timestamp'])
@pytest.mark.parametrize('milliseconds,expected',[(0,False),(130,False),(250,False),(251,True),(1000,True)])
def test_bounded_future_tolerance_preserves_original_timestamps(component,milliseconds,expected):
    value=replace(snapshot(),**{component:NOW+timedelta(milliseconds=milliseconds)})
    before=asdict(value)
    result=QuickScalperPolicy(tolerant()).assess(value)
    assert (result.reason=='PROVIDER_DATA_STALE') is expected
    assert asdict(value)==before


@pytest.mark.parametrize('component',['bid_timestamp','ask_timestamp'])
def test_old_book_remains_rejected_with_future_tolerance(component):
    value=replace(snapshot(),**{component:NOW-timedelta(seconds=5,microseconds=1)})
    assert QuickScalperPolicy(tolerant()).assess(value).reason=='PROVIDER_DATA_STALE'


def test_default_and_legacy_policy_continue_to_reject_any_future_timestamp():
    value=replace(snapshot(),last_timestamp=NOW+timedelta(milliseconds=1))
    assert QuickScalperPolicy(config()).assess(value).reason=='PROVIDER_DATA_STALE'
    assert QuickScalperConfig().provider_future_tolerance_seconds==0


@pytest.mark.parametrize('value',['-.001','.251','NaN','Infinity'])
def test_invalid_tolerance_is_refused(value):
    with pytest.raises(ValueError,match='250ms'):replace(config(),provider_future_tolerance_seconds=D(value))


def test_nonexperiment_config_cannot_enable_future_tolerance():
    with pytest.raises(ValueError,match='PAPER profile'):
        QuickScalperConfig(provider_future_tolerance_seconds=D('.25'))


def test_clock_policy_is_explicit_sealed_and_does_not_change_sizing_or_expiry(tmp_path):
    for enabled in (False,True):
        root=tmp_path/str(enabled)
        create(root,lambda:NOW,profile='GROK_WARRIOR_ACCOUNT_V6',extended_sessions=True,clock_tolerance=enabled)
        path,spec=load(root/'experiment.json',lambda:NOW)
        exp=PaperExperiment(path,spec,clock=lambda:NOW)
        actual=exp.scalper_config(QuickScalperConfig(enabled=True))
        assert actual.provider_future_tolerance_seconds==(D('.25') if enabled else 0)
        assert actual.provider_freshness_seconds==5
        assert actual.maximum_holding_seconds==300
        assert exp.account_sizing and spec['limits']['maximum_quantity'] is None
        if enabled:
            assert spec['scalper_clock_policy']==CLOCK_POLICY
            spec['scalper_clock_policy']['maximum_future_ms']=251
            path.write_text(json.dumps(spec))
            with pytest.raises(ValueError,match='CLOCK_POLICY_INVALID'):load(path,lambda:NOW)
    with pytest.raises(ValueError,match='ACCOUNT_PAPER_CLOCK'):
        create(tmp_path/'wrong',lambda:NOW,profile='GROK_WARRIOR_V5',clock_tolerance=True)


def test_launch_preparation_is_manual_and_clock_policy_only_begins_at_start(tmp_path,monkeypatch):
    from app.asset_modules.paper_day_setup import prepare
    monkeypatch.setenv('XAI_API_KEY','synthetic-only');monkeypatch.setenv('ATLAS_GROK_MODEL','grok-test')
    repo=tmp_path/'repo';repo.mkdir();output=tmp_path/'trial'
    result=prepare(repo,output,profile='GROK_WARRIOR_ACCOUNT_V6',extended_sessions=True,clock_tolerance=True)
    text=(output/'Start-Atlas-Paper-Trial.ps1').read_text()
    assert '--clock-tolerance' in text and "'LIVE_TRADING_ENABLED' = 'false'" in text
    assert result['scalper_clock_tolerance_ms']==250
    assert result['prepared_only'] and not result['atlas_started']
    assert not (output/'experiment.json').exists()


def test_clock_experiment_cannot_be_composed_in_live_environment(tmp_path,monkeypatch):
    from app.equity_overhaul.paper_experiment import configured,ENV
    import app.asset_modules.grok_execution_trial as lease
    create(tmp_path,lambda:NOW,profile='GROK_WARRIOR_ACCOUNT_V6',clock_tolerance=True)
    monkeypatch.setenv(ENV,str(tmp_path/'experiment.json'))
    monkeypatch.setattr(lease,'configured',lambda _:None)
    for mode,environment,live in [('LIVE','PAPER',False),('PAPER','LIVE',False),('PAPER','PAPER',True)]:
        cfg=NS(environment=NS(value=environment),live_trading_enabled=live,grok_paper_entries_enabled=False,daily_research_enabled=True)
        with pytest.raises(ValueError,match='REQUIRES_PAPER'):configured(cfg,mode)


def test_timing_option_changes_only_the_clock_field_of_scalper_configuration(tmp_path):
    create(tmp_path,lambda:NOW,profile='GROK_WARRIOR_ACCOUNT_V6',extended_sessions=True,clock_tolerance=True)
    path,spec=load(tmp_path/'experiment.json',lambda:NOW)
    enabled=PaperExperiment(path,spec,clock=lambda:NOW).scalper_config(QuickScalperConfig(enabled=True))
    original={k:v for k,v in spec.items() if k!='scalper_clock_policy'}
    strict=PaperExperiment(path,original,clock=lambda:NOW).scalper_config(QuickScalperConfig(enabled=True))
    assert replace(enabled,provider_future_tolerance_seconds=D(0))==strict
