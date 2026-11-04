from dataclasses import replace
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import json
import pytest
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,EXTENDED_ENTRY_SESSIONS
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from app.market.scalp_session import scalp_entry_window,scalp_entry_deadline

ET=ZoneInfo('America/New_York')
NOW=datetime(2026,11,5,9,tzinfo=ET)


def test_extended_policy_is_explicit_bound_to_spec_and_both_engine_configurations(tmp_path):
    create(tmp_path,lambda:NOW,profile='GROK_WARRIOR_V5',extended_sessions=True)
    path,spec=load(tmp_path/'experiment.json',lambda:NOW)
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
    assert p.warrior_config(WarriorMomentumConfig()).entry.allowed_sessions==EXTENDED_ENTRY_SESSIONS
    assert p.scalper_config(QuickScalperConfig()).allowed_sessions==EXTENDED_ENTRY_SESSIONS
    assert spec['limits']['hours']==8 and spec['limits']['maximum_entry_attempts']==50
    assert 'AFTER_HOURS' in p.notice()
    spec['entry_sessions'].append('UNKNOWN');path.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='SESSION_POLICY'):load(path,lambda:NOW)


def test_ordinary_profile_keeps_its_original_sessions_and_rejects_legacy_extension(tmp_path):
    create(tmp_path/'ordinary',lambda:NOW,profile='GROK_WARRIOR_V5')
    path,spec=load(tmp_path/'ordinary/experiment.json',lambda:NOW)
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
    assert p.warrior_config(WarriorMomentumConfig()).entry.allowed_sessions==frozenset({'PREMARKET','REGULAR'})
    assert p.scalper_config(QuickScalperConfig()).allowed_sessions==frozenset({'PREMARKET','REGULAR'})
    with pytest.raises(ValueError):create(tmp_path/'legacy',lambda:NOW,extended_sessions=True)


@pytest.mark.parametrize('at,session', [('2026-11-05T09:29:59','PREMARKET'),
    ('2026-11-05T15:59:59','REGULAR'),('2026-11-05T16:00','AFTER_HOURS'),
    ('2026-11-05T19:50','AFTER_HOURS'),('2026-11-27T13:00','AFTER_HOURS')])
def test_continuous_window_and_recovered_authority_cross_only_opted_in_boundaries(at,session):
    at=datetime.fromisoformat(at).replace(tzinfo=ET)
    actual,deadline=scalp_entry_window(at,extended_sessions=True)
    assert actual==session and deadline.hour==19 and deadline.minute==55
    assert scalp_entry_deadline(at,{'scalp_extended_entry_sessions':True})==deadline
    assert scalp_entry_deadline(at,{'scalp_extended_entry_sessions':True,
        'scalp_entry_session_deadline':(at-timedelta(seconds=1)).isoformat()})<at


@pytest.mark.parametrize('at',['2026-11-05T03:59','2026-11-05T20:00','2026-11-07T12:00','2026-12-25T12:00'])
def test_extension_does_not_create_a_quote_session_on_closed_calendar(at):
    at=datetime.fromisoformat(at).replace(tzinfo=ET)
    assert scalp_entry_window(at,extended_sessions=True) is None
    assert scalp_entry_deadline(at,{'scalp_extended_entry_sessions':True})==at


def test_recovery_keeps_old_restrictions_and_corrupt_flags_expire_entry_authority():
    at=NOW.replace(hour=17)
    for flag in (False,'true',1,None):
        assert scalp_entry_deadline(at,{'scalp_extended_entry_sessions':flag})==at
    assert scalp_entry_deadline(at,{})==at


def test_prepared_launcher_creates_new_opt_in_only_at_manual_launch(tmp_path,monkeypatch):
    from app.asset_modules.paper_day_setup import prepare
    monkeypatch.setenv('XAI_API_KEY','fixture-key');monkeypatch.setenv('ATLAS_GROK_MODEL','grok-test')
    repo=tmp_path/'repo';repo.mkdir();out=tmp_path/'trial'
    report=prepare(repo,out,profile='GROK_WARRIOR_V5',extended_sessions=True)
    assert report['extended_sessions'] and 'AFTER_HOURS' in report['entry_sessions']
    assert '--extended-sessions' in (out/'Start-Atlas-Paper-Trial.ps1').read_text()
    assert not (out/'experiment.json').exists()
    assert not (out/'Start-Grok-Intentions.ps1').exists()
