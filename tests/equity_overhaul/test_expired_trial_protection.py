from datetime import UTC,datetime,timedelta
import pytest
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment

NOW=datetime(2026,11,10,19,tzinfo=UTC)


def test_expired_spec_can_be_loaded_for_protection_without_renewing_entry_authority(tmp_path):
    create(tmp_path,lambda:NOW,profile='GROK_WARRIOR_ACCOUNT_V6',clock_tolerance=True)
    path=tmp_path/'experiment.json';before=path.read_bytes();later=NOW+timedelta(hours=9)
    with pytest.raises(ValueError,match='EXPIRED'):load(path,lambda:later)
    _,spec=load(path,lambda:later,allow_expired=True)
    exp=PaperExperiment(path,spec,clock=lambda:later,timer=lambda:0);exp.capture_ready=lambda:True
    assert not exp.active()
    assert path.read_bytes()==before
    assert spec['expires_at']==(NOW+timedelta(hours=8)).isoformat()
    with pytest.raises(ValueError,match='FUTURE'):load(path,lambda:NOW-timedelta(seconds=1),allow_expired=True)


def test_expired_launcher_preflight_keeps_original_id_and_expiry(tmp_path,monkeypatch,capsys):
    import sys
    import app.equity_overhaul.paper_experiment as module
    create(tmp_path,lambda:NOW,profile='GROK_WARRIOR_ACCOUNT_V6',clock_tolerance=True)
    path=tmp_path/'experiment.json';before=path.read_bytes()
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return NOW+timedelta(hours=9)
    monkeypatch.setattr(module,'datetime',Later)
    monkeypatch.setattr(sys,'argv',['preflight','--create',str(tmp_path),'--profile','GROK_WARRIOR_ACCOUNT_V6','--clock-tolerance'])
    module.main()
    assert 'PROTECTION_ONLY' in capsys.readouterr().out
    assert path.read_bytes()==before


def test_explicit_24_hour_account_trial_stays_active_through_next_regular_session(tmp_path):
    import json
    # 8:42 p.m. CST: the old eight-hour window ended before regular trading.
    evening=datetime(2026,11,11,2,42,tzinfo=UTC)
    create(tmp_path,lambda:evening,profile='GROK_WARRIOR_ACCOUNT_V6',hours=24)
    path=tmp_path/'experiment.json'
    noon=evening+timedelta(hours=16)
    _,spec=load(path,lambda:noon)
    exp=PaperExperiment(path,spec,clock=lambda:noon,timer=lambda:0)
    exp.capture_ready=lambda:True
    assert exp.active() and spec['limits']['maximum_quantity'] is None
    assert spec['expires_at']==(evening+timedelta(hours=24)).isoformat()
    with pytest.raises(ValueError,match='EXPIRED'):load(path,lambda:evening+timedelta(hours=24))
    spec['limits']['hours']=25;path.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='DURATION'):load(path,lambda:noon)


def test_24_hours_requires_explicit_account_profile(tmp_path):
    with pytest.raises(ValueError,match='DURATION'):create(tmp_path,hours=24)
    assert not (tmp_path/'experiment.json').exists()
