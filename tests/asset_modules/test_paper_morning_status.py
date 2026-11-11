import json
from app.asset_modules.paper_morning_status import report
import pytest


def test_read_only_status_does_not_create_trial_or_budget(tmp_path):
    result=report(tmp_path)
    assert not result['trial_started'] and result['grok_reserved_attempts']==0
    assert list(tmp_path.iterdir())==[]


def test_wrong_pointer_cannot_report_another_trial(tmp_path):
    (tmp_path/'experiment.json').write_text(json.dumps({'trial_id':'a','expires_at':'2026-11-03T22:00:00+00:00'}))
    (tmp_path/'recording-session.json').write_text(json.dumps({'trial_id':'b','session':'unread'}))
    with pytest.raises(ValueError,match='MISMATCH'):report(tmp_path)


def test_expiry_and_pause_are_visible_without_enabling_or_mutating_entries(tmp_path):
    from datetime import UTC,datetime,timedelta
    from app.equity_overhaul.paper_experiment import create
    create(tmp_path,lambda:datetime.now(UTC)-timedelta(hours=25),profile='GROK_WARRIOR_ACCOUNT_V6',hours=24)
    (tmp_path/'PAUSE_ENTRIES').touch()
    before=(tmp_path/'experiment.json').read_bytes()
    result=report(tmp_path)
    assert result['entry_window_state']=='EXPIRED'
    assert result['entry_blockers']==['TRIAL_EXPIRED','PAUSE_ENTRIES']
    assert result['entry_window_hours']==24 and not result['orders_enabled_by_this_check']
    assert (tmp_path/'experiment.json').read_bytes()==before
